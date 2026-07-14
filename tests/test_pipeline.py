import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, create_autospec

from PIL import Image

from app.config.loader import (
    FiltersConfig,
    HeadlineCardConfig,
    ImagesConfig,
    RewriteConfig,
    ScoringWeights,
    UniquifyConfig,
    WatermarkConfig,
)
from app.core.llm.classifier import ClassificationError, ClassificationResult
from app.core.llm.client import LLMClient
from app.core.pipeline import (
    _filter_watermarked_photos,
    _prepare_images,
    _prepare_video,
    process_fetched_post,
)
from app.core.monitoring.models import FetchedPost
from app.db.repository import Repository, init_db, make_engine

WEIGHTS = ScoringWeights(
    news_value=0.35, keyword_match=0.25, source_views=0.20, freshness=0.10, source_priority=0.10
)
REWRITE_CONFIG = RewriteConfig(style="viral", max_length_chars=900, headline_variants=3)


def make_repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    return Repository(engine)


def make_filters(**overrides) -> FiltersConfig:
    defaults = dict(
        min_score=75,
        important_score_threshold=88,
        duplicate_similarity_threshold=0.85,
        min_views=500,
        stop_words=["скидка"],
        required_keywords_boost=True,
        whitelist_keywords=["Госдума"],
        blacklist_keywords=[],
    )
    defaults.update(overrides)
    return FiltersConfig(**defaults)


def make_post(**overrides) -> FetchedPost:
    defaults = dict(
        external_id="1",
        text="Госдума приняла новый закон о бюджете",
        post_type="text",
        views=1000,
        published_at=datetime.now(timezone.utc),
        has_media=False,
    )
    defaults.update(overrides)
    return FetchedPost(**defaults)


def test_process_fetched_post_accepts_good_news(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Новостной канал", url="https://t.me/x", priority=8)
    client = Mock(spec=LLMClient)
    client.load_prompt.side_effect = lambda name: f"<{name}>"

    with_classification_mock = Mock(
        side_effect=lambda *a, **k: ClassificationResult(
            is_news=True, category="политика", score=90, reasons=["важно"], reject_reason=None
        )
    )

    import app.core.pipeline as pipeline_module

    original_classify = pipeline_module.classify_post
    original_rewrite = pipeline_module.rewrite_post
    original_headlines = pipeline_module.generate_headlines
    pipeline_module.classify_post = with_classification_mock
    # create_autospec (не Mock(spec=...) — тот НЕ проверяет сигнатуру вызова): защита
    # от рассинхрона сигнатуры. Был реальный прод-баг 2026-07-08 — pipeline звал
    # rewrite_post без style, обычный Mock() это не ловил.
    rewrite_autospec = create_autospec(original_rewrite, return_value="Переписанный текст новости")
    pipeline_module.rewrite_post = rewrite_autospec
    pipeline_module.generate_headlines = Mock(return_value=["Заголовок один", "Заголовок два"])

    try:
        outcome = process_fetched_post(
            repo,
            source,
            make_post(),
            llm_client=client,
            filters=make_filters(),
            scoring_weights=WEIGHTS,
            rewrite_config=REWRITE_CONFIG,
            max_post_age_hours=24,
        )
    finally:
        pipeline_module.classify_post = original_classify
        pipeline_module.rewrite_post = original_rewrite
        pipeline_module.generate_headlines = original_headlines

    assert outcome is not None
    assert outcome.rejected is None
    assert outcome.accepted is not None
    assert outcome.accepted.status == "queued"
    assert outcome.accepted.headline == "Заголовок один"
    queued = repo.list_processed_posts(status="queued")
    assert len(queued) == 1


def test_process_fetched_post_logs_pipeline_stages(tmp_path, caplog):
    """ТЗ 2026-07-10: сквозное логирование этапов — по логам должно быть видно
    путь поста от приёма до постановки в очередь без похода в БД."""
    import logging

    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Новостной канал", url="https://t.me/x", priority=8)
    client = Mock(spec=LLMClient)
    client.load_prompt.side_effect = lambda name: f"<{name}>"

    import app.core.pipeline as pipeline_module

    original_classify = pipeline_module.classify_post
    original_rewrite = pipeline_module.rewrite_post
    original_headlines = pipeline_module.generate_headlines
    pipeline_module.classify_post = Mock(
        return_value=ClassificationResult(
            is_news=True, category="политика", score=90, reasons=["важно"], reject_reason=None
        )
    )
    pipeline_module.rewrite_post = create_autospec(
        original_rewrite, return_value="Переписанный текст новости"
    )
    pipeline_module.generate_headlines = Mock(return_value=["Заголовок"])

    try:
        with caplog.at_level(logging.INFO, logger="monitoring"):
            process_fetched_post(
                repo,
                source,
                make_post(),
                llm_client=client,
                filters=make_filters(),
                scoring_weights=WEIGHTS,
                rewrite_config=REWRITE_CONFIG,
                max_post_age_hours=24,
            )
    finally:
        pipeline_module.classify_post = original_classify
        pipeline_module.rewrite_post = original_rewrite
        pipeline_module.generate_headlines = original_headlines

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "принят в обработку" in messages
    assert "рерайт готов" in messages
    assert "медиа подготовлено" in messages
    assert "поставлен в очередь публикации" in messages


def test_process_fetched_post_does_not_shorten_rewrite_below_original_length(tmp_path):
    """Запрос пользователя 2026-07-09: объём рерайта ≈ как у оригинала, не сокращая и
    не раздувая. max_length = длина оригинала — не заставляет ужимать (смысл цел) и не
    даёт растягивать короткие посты до конфигового потолка (раньше был max(900, len))."""
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x", priority=8)
    client = Mock(spec=LLMClient)
    client.load_prompt.side_effect = lambda name: f"<{name}>"

    text = "Госдума приняла закон о бюджете на следующий год после долгих обсуждений."
    short_post = make_post(text=text)

    import app.core.pipeline as pipeline_module

    original_classify = pipeline_module.classify_post
    original_rewrite = pipeline_module.rewrite_post
    original_headlines = pipeline_module.generate_headlines
    pipeline_module.classify_post = Mock(
        return_value=ClassificationResult(
            is_news=True, category="политика", score=90, reasons=["важно"], reject_reason=None
        )
    )
    rewrite_mock = create_autospec(original_rewrite, return_value="Переписанный текст новости")
    pipeline_module.rewrite_post = rewrite_mock
    pipeline_module.generate_headlines = Mock(return_value=["Заголовок"])

    try:
        process_fetched_post(
            repo,
            source,
            short_post,
            llm_client=client,
            filters=make_filters(),
            scoring_weights=WEIGHTS,
            rewrite_config=REWRITE_CONFIG,
            max_post_age_hours=24,
        )
    finally:
        pipeline_module.classify_post = original_classify
        pipeline_module.rewrite_post = original_rewrite
        pipeline_module.generate_headlines = original_headlines

    _, kwargs = rewrite_mock.call_args
    # Потолок = длина оригинала: не ужимаем и не раздуваем — держим объём исходника.
    assert kwargs["max_length"] == len(text)


def test_filter_watermarked_photos_drops_when_watermark_not_removable(monkeypatch):
    """По запросу пользователя 2026-07-05: фото с чужим знаком по центру/на самом
    сюжете (обрезкой не убрать) не должны идти в публикацию — пайплайн вместо них
    падает на сток-фолбэк."""
    import app.core.pipeline as pipeline_module

    client = Mock(spec=LLMClient)

    def fake_locate(_client, path):
        return None if str(path) == "watermarked.jpg" else set()

    monkeypatch.setattr(pipeline_module, "locate_foreign_watermark", fake_locate)

    result = _filter_watermarked_photos(client, ["clean.jpg", "watermarked.jpg"])

    assert result == ["clean.jpg"]


def test_filter_watermarked_photos_keeps_remote_urls_unchecked(monkeypatch):
    """VK отдаёт прямые URL — на этом шаге локального файла для vision-анализа ещё
    нет, поэтому такие элементы проходят без проверки (не regression, просто scope)."""
    import app.core.pipeline as pipeline_module

    client = Mock(spec=LLMClient)
    locate_mock = Mock(return_value=None)
    monkeypatch.setattr(pipeline_module, "locate_foreign_watermark", locate_mock)

    result = _filter_watermarked_photos(client, ["https://vk.com/photo1.jpg"])

    assert result == ["https://vk.com/photo1.jpg"]
    locate_mock.assert_not_called()


def test_filter_watermarked_photos_crops_out_removable_watermark_and_keeps_original(
    tmp_path, monkeypatch
):
    """По прямому запросу пользователя 2026-07-05: "хочу чтобы ты в точь в точь
    находил оригинальное фото только без чужого монтажа и вотермарков" — если знак
    можно убрать обрезкой края, используем ЭТО ЖЕ фото очищенным, а не сток."""
    import app.core.pipeline as pipeline_module

    client = Mock(spec=LLMClient)
    monkeypatch.setattr(pipeline_module, "locate_foreign_watermark", lambda *a, **k: {"top"})

    source_path = tmp_path / "source.jpg"
    Image.new("RGB", (400, 400), color="blue").save(source_path)

    result = _filter_watermarked_photos(client, [str(source_path)])

    assert len(result) == 1
    cleaned_path = Path(result[0])
    assert cleaned_path != source_path
    assert cleaned_path.exists()
    cleaned_image = Image.open(cleaned_path)
    assert cleaned_image.height < 400  # верхняя полоса обрезана
    assert cleaned_image.width == 400  # ширина не тронута


def test_filter_watermarked_photos_keeps_clean_photo_unchanged(tmp_path, monkeypatch):
    import app.core.pipeline as pipeline_module

    client = Mock(spec=LLMClient)
    monkeypatch.setattr(pipeline_module, "locate_foreign_watermark", lambda *a, **k: set())

    source_path = tmp_path / "source.jpg"
    Image.new("RGB", (400, 400), color="blue").save(source_path)

    result = _filter_watermarked_photos(client, [str(source_path)])

    assert result == [str(source_path)]


def _images_config(count_per_post: int) -> ImagesConfig:
    return ImagesConfig(
        providers_order=["source", "pexels"],
        count_per_post=count_per_post,
        target_aspect_ratio="4:5",
        uniquify=UniquifyConfig(enabled=False),
    )


def _watermark_config() -> WatermarkConfig:
    return WatermarkConfig(logo_path="assets/logo.png", position="top-right", opacity=65, margin_px=20)


def test_prepare_video_returns_raw_path_when_video_watermark_disabled():
    """Запрос пользователя 2026-07-05: на видео вотермарк/монтаж не ставим — видео
    уходит как есть (video_enabled=False), без вызова ffmpeg-watermark."""
    wm = WatermarkConfig(
        logo_path="assets/logo.png", position="top-right", opacity=65, margin_px=20,
        video_enabled=False,
    )
    result = _prepare_video(
        raw_post_id=1, post_video_path="/some/video.mp4", watermark_config=wm, images_config=None
    )
    assert result == "/some/video.mp4"


def test_prepare_images_uses_all_own_photos_not_count_per_post(monkeypatch):
    """Запрос пользователя 2026-07-05: свои фото поста берём ВСЕ (не ограничиваемся
    count_per_post, который для стока = 1). Первое получит заголовок, остальные — фейд."""
    import app.core.pipeline as pipeline_module

    monkeypatch.setattr(
        pipeline_module, "_filter_watermarked_photos", lambda *a, **k: ["p1.jpg", "p2.jpg", "p3.jpg"]
    )
    captured = {}

    def fake_prepare(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(pipeline_module, "prepare_images_for_post", fake_prepare)

    _prepare_images(
        Mock(spec=LLMClient),
        raw_post_id=1,
        post_media_urls=["p1.jpg", "p2.jpg", "p3.jpg"],
        rewritten_text="текст",
        images_config=_images_config(count_per_post=1),
        watermark_config=_watermark_config(),
        headline_card_config=HeadlineCardConfig(),
        image_providers={},
    )

    assert captured["count"] == 3  # все три своих, не count_per_post=1


def test_prepare_images_limits_stock_to_count_per_post(monkeypatch):
    """Нет своих фото (все отфильтрованы) — сток берём только count_per_post (1)."""
    import app.core.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "_filter_watermarked_photos", lambda *a, **k: [])
    monkeypatch.setattr(pipeline_module, "_safe_image_query", lambda *a, **k: "запрос")
    captured = {}

    def fake_prepare(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(pipeline_module, "prepare_images_for_post", fake_prepare)

    _prepare_images(
        Mock(spec=LLMClient),
        raw_post_id=2,
        post_media_urls=[],
        rewritten_text="текст",
        images_config=_images_config(count_per_post=1),
        watermark_config=_watermark_config(),
        headline_card_config=HeadlineCardConfig(),
        image_providers={"pexels": Mock()},
    )

    assert captured["count"] == 1


def test_prepare_images_keep_original_returns_media_untouched(monkeypatch):
    """Лёгкий режим (keep_original) 2026-07-07: медиа поста уходят ОРИГИНАЛАМИ —
    без детекции чужих знаков, монтажа/вотермарка и сток-фолбэка. Пайплайн просто
    отдаёт скачанные файлы."""
    import app.core.pipeline as pipeline_module

    filter_mock = Mock()
    prepare_mock = Mock()
    monkeypatch.setattr(pipeline_module, "_filter_watermarked_photos", filter_mock)
    monkeypatch.setattr(pipeline_module, "prepare_images_for_post", prepare_mock)

    config = ImagesConfig(
        providers_order=["source"],
        count_per_post=1,
        target_aspect_ratio="1:1",
        uniquify=UniquifyConfig(enabled=False),
        keep_original=True,
    )

    result = _prepare_images(
        Mock(spec=LLMClient),
        raw_post_id=1,
        post_media_urls=["a.jpg", "b.jpg"],
        rewritten_text="текст",
        images_config=config,
        watermark_config=_watermark_config(),
        headline_card_config=HeadlineCardConfig(),
        image_providers={},
    )

    assert result == ["a.jpg", "b.jpg"]
    filter_mock.assert_not_called()
    prepare_mock.assert_not_called()


def test_process_fetched_post_applies_video_watermark_when_video_present(tmp_path, monkeypatch):
    """Реальный ffmpeg-вызов (не мок) — история проекта уже показала, что моки
    пропускают баги, которые ловит только реальный внешний инструмент."""
    import app.core.video.watermark as video_watermark_module

    monkeypatch.setattr(video_watermark_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(video_watermark_module, "OUTPUT_DIR", tmp_path / "output")

    (tmp_path / "assets").mkdir()
    Image.new("RGBA", (200, 100), color=(255, 0, 0, 255)).save(tmp_path / "assets" / "logo.png")

    video_path = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10",
            "-pix_fmt", "yuv420p", str(video_path),
        ],
        check=True, capture_output=True,
    )

    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x", priority=8)
    client = Mock(spec=LLMClient)
    client.load_prompt.side_effect = lambda name: f"<{name}>"

    import app.core.pipeline as pipeline_module

    original_classify = pipeline_module.classify_post
    original_rewrite = pipeline_module.rewrite_post
    original_headlines = pipeline_module.generate_headlines
    pipeline_module.classify_post = Mock(
        return_value=ClassificationResult(
            is_news=True, category="политика", score=90, reasons=["важно"], reject_reason=None
        )
    )
    pipeline_module.rewrite_post = create_autospec(original_rewrite, return_value="Переписанный текст новости")
    pipeline_module.generate_headlines = Mock(return_value=["Заголовок"])

    watermark_config = WatermarkConfig(
        logo_path="assets/logo.png",
        position="top-right",
        opacity=65,
        margin_px=20,
        size_ratio=0.2,
    )

    try:
        outcome = process_fetched_post(
            repo,
            source,
            make_post(video_path=str(video_path)),
            llm_client=client,
            filters=make_filters(),
            scoring_weights=WEIGHTS,
            rewrite_config=REWRITE_CONFIG,
            max_post_age_hours=24,
            watermark_config=watermark_config,
        )
    finally:
        pipeline_module.classify_post = original_classify
        pipeline_module.rewrite_post = original_rewrite
        pipeline_module.generate_headlines = original_headlines

    assert outcome is not None
    assert outcome.accepted is not None
    assert outcome.accepted.video_path is not None
    assert Path(outcome.accepted.video_path).exists()


def test_process_fetched_post_skips_already_seen_external_id(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    repo.create_raw_post(source_id=source.id, external_id="1", raw_text="старый пост")
    client = Mock(spec=LLMClient)

    outcome = process_fetched_post(
        repo,
        source,
        make_post(external_id="1"),
        llm_client=client,
        filters=make_filters(),
        scoring_weights=WEIGHTS,
        rewrite_config=REWRITE_CONFIG,
        max_post_age_hours=24,
    )

    assert outcome is None


def test_process_fetched_post_rejects_structural_non_news(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    client = Mock(spec=LLMClient)

    outcome = process_fetched_post(
        repo,
        source,
        make_post(post_type="poll"),
        llm_client=client,
        filters=make_filters(),
        scoring_weights=WEIGHTS,
        rewrite_config=REWRITE_CONFIG,
        max_post_age_hours=24,
    )

    assert outcome.accepted is None
    assert outcome.rejected is not None
    assert "poll" in outcome.rejected.reason


def test_process_fetched_post_rejects_blacklisted_word(tmp_path, caplog):
    """Регрессия: отказы логируются (раньше были видны только в БД), чтобы не
    приходилось лезть в rejected_posts руками для диагностики."""
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    client = Mock(spec=LLMClient)

    with caplog.at_level("INFO", logger="monitoring"):
        outcome = process_fetched_post(
            repo,
            source,
            make_post(text="В магазине объявили скидка на технику"),
            llm_client=client,
            filters=make_filters(),
            scoring_weights=WEIGHTS,
            rewrite_config=REWRITE_CONFIG,
            max_post_age_hours=24,
        )

    assert "отклонён" in caplog.text

    assert outcome.accepted is None
    assert "скидка" in outcome.rejected.reason


def test_process_fetched_post_rejects_near_duplicate_of_earlier_post(tmp_path):
    from app.core.filtering.deduplication import compute_simhash

    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    client = Mock(spec=LLMClient)

    earlier_text = "Госдума приняла новый закон о бюджете на 2026 год"
    repo.create_raw_post(
        source_id=source.id,
        external_id="0",
        raw_text=earlier_text,
        content_hash=compute_simhash(earlier_text),
    )

    outcome = process_fetched_post(
        repo,
        source,
        make_post(external_id="1", text="Госдума приняла новый закон о бюджете на 2027 год"),
        llm_client=client,
        filters=make_filters(),
        scoring_weights=WEIGHTS,
        rewrite_config=REWRITE_CONFIG,
        max_post_age_hours=24,
    )

    assert outcome.accepted is None
    assert "SimHash" in outcome.rejected.reason


def test_process_fetched_post_rejects_on_classification_error(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    client = Mock(spec=LLMClient)

    import app.core.pipeline as pipeline_module

    original_classify = pipeline_module.classify_post
    pipeline_module.classify_post = Mock(side_effect=ClassificationError("плохой JSON"))

    try:
        outcome = process_fetched_post(
            repo,
            source,
            make_post(),
            llm_client=client,
            filters=make_filters(),
            scoring_weights=WEIGHTS,
            rewrite_config=REWRITE_CONFIG,
            max_post_age_hours=24,
        )
    finally:
        pipeline_module.classify_post = original_classify

    assert outcome.accepted is None
    assert outcome.rejected.reason == "error_classification"


def test_process_fetched_post_filters_disabled_accepts_non_news(tmp_path):
    """Кино/мемы (filters_enabled=False): пост принимается без LLM-гейта новостей и
    порога скоринга — classify_post даже не вызывается, «лить всё подряд»."""
    repo = make_repo(tmp_path)
    channel = repo.create_channel(name="Кино")
    source = repo.create_source(
        type="vk", name="Кинопремьеры", url="58170807", channel_id=channel.id
    )
    client = Mock(spec=LLMClient)
    client.load_prompt.side_effect = lambda name: f"<{name}>"

    import app.core.pipeline as pipeline_module

    original_classify = pipeline_module.classify_post
    original_rewrite = pipeline_module.rewrite_post
    original_headlines = pipeline_module.generate_headlines
    classify_spy = Mock()
    pipeline_module.classify_post = classify_spy
    pipeline_module.rewrite_post = create_autospec(
        original_rewrite, return_value="Рерайт описания фильма"
    )
    pipeline_module.generate_headlines = Mock(return_value=["Ундина"])

    try:
        outcome = process_fetched_post(
            repo,
            source,
            make_post(text="Ундина (2009) — драма про рыбака и русалку", external_id="9"),
            llm_client=client,
            # даже с недостижимым порогом и чужим whitelist пост проходит — фильтры off
            filters=make_filters(min_score=99, whitelist_keywords=["Госдума"]),
            scoring_weights=WEIGHTS,
            rewrite_config=REWRITE_CONFIG,
            max_post_age_hours=24,
            filters_enabled=False,
        )
    finally:
        pipeline_module.classify_post = original_classify
        pipeline_module.rewrite_post = original_rewrite
        pipeline_module.generate_headlines = original_headlines

    assert outcome is not None
    assert outcome.rejected is None
    assert outcome.accepted is not None
    assert outcome.accepted.status == "queued"
    classify_spy.assert_not_called()


def test_process_fetched_post_filters_disabled_still_dedups(tmp_path):
    """Даже в режиме «лить всё» дедуп сохраняется — один и тот же пост не уходит дважды."""
    repo = make_repo(tmp_path)
    channel = repo.create_channel(name="Кино")
    source = repo.create_source(
        type="vk", name="Кинопремьеры", url="58170807", channel_id=channel.id
    )
    client = Mock(spec=LLMClient)
    client.load_prompt.side_effect = lambda name: f"<{name}>"

    import app.core.pipeline as pipeline_module

    original_rewrite = pipeline_module.rewrite_post
    original_headlines = pipeline_module.generate_headlines
    pipeline_module.rewrite_post = create_autospec(original_rewrite, return_value="Рерайт")
    pipeline_module.generate_headlines = Mock(return_value=["Заголовок"])

    text = "Ундина 2009 драма мелодрама детектив про рыбака Сиракуза и его дочь Энни"
    try:
        first = process_fetched_post(
            repo, source, make_post(text=text, external_id="1"),
            llm_client=client, filters=make_filters(), scoring_weights=WEIGHTS,
            rewrite_config=REWRITE_CONFIG, max_post_age_hours=24, filters_enabled=False,
        )
        second = process_fetched_post(
            repo, source, make_post(text=text, external_id="2"),
            llm_client=client, filters=make_filters(), scoring_weights=WEIGHTS,
            rewrite_config=REWRITE_CONFIG, max_post_age_hours=24, filters_enabled=False,
        )
    finally:
        pipeline_module.rewrite_post = original_rewrite
        pipeline_module.generate_headlines = original_headlines

    assert first.accepted is not None
    assert second.accepted is None
    assert second.rejected.reason == "дубль по SimHash"


def test_prepare_images_keep_original_falls_back_to_stock_when_no_own_photo(monkeypatch):
    """keep_original + нет своего фото (2026-07-09): берём сток (Pexels) по запросу из
    текста, без вотермарка — пост без картинки читается плохо."""
    import app.core.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "_safe_image_query", lambda *a, **k: "город ночь")
    monkeypatch.setattr(pipeline_module, "resolve_to_local_file", lambda result, path: path)

    provider = Mock()
    provider.search.return_value = ["http://img1"]
    config = ImagesConfig(
        providers_order=["source", "pexels"],
        count_per_post=1,
        target_aspect_ratio="1:1",
        uniquify=UniquifyConfig(enabled=False),
        keep_original=True,
    )

    result = _prepare_images(
        Mock(spec=LLMClient),
        raw_post_id=7,
        post_media_urls=[],  # нет своих фото
        rewritten_text="текст",
        images_config=config,
        watermark_config=_watermark_config(),
        headline_card_config=HeadlineCardConfig(),
        image_providers={"pexels": provider},
    )

    assert result is not None and len(result) == 1
    provider.search.assert_called_once_with("город ночь", 1)


def test_prepare_images_keep_original_no_photo_no_stock_returns_none(monkeypatch):
    """keep_original + нет своего фото + нет сток-провайдеров → пост без фото (None)."""
    config = ImagesConfig(
        providers_order=["source"],
        count_per_post=1,
        target_aspect_ratio="1:1",
        uniquify=UniquifyConfig(enabled=False),
        keep_original=True,
    )

    result = _prepare_images(
        Mock(spec=LLMClient),
        raw_post_id=8,
        post_media_urls=[],
        rewritten_text="текст",
        images_config=config,
        watermark_config=_watermark_config(),
        headline_card_config=HeadlineCardConfig(),
        image_providers={},
    )

    assert result is None


def test_prepare_images_movie_title_mode_searches_by_movie_name(monkeypatch):
    """Кино-канал (image_query_mode='movie_title'): не берём фото источника/сток —
    ищем кадры конкретного фильма через провайдер из image_search_providers."""
    import app.core.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "_safe_movie_query", lambda *a, **k: "Ундина (2009) кадр из фильма")
    captured = {}

    def fake_prepare(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(pipeline_module, "prepare_images_for_post", fake_prepare)

    _prepare_images(
        Mock(spec=LLMClient),
        raw_post_id=42,
        post_media_urls=["a.jpg"],  # даже если есть своё фото — в movie_title его не берём
        rewritten_text="рецензия на Ундину",
        images_config=_images_config(count_per_post=1),
        watermark_config=_watermark_config(),
        headline_card_config=HeadlineCardConfig(),
        image_providers={"google": Mock(), "pexels": Mock()},
        image_query_mode="movie_title",
        image_search_providers=["google"],
    )

    assert captured["providers_order"] == ["google"]
    assert captured["query"] == "Ундина (2009) кадр из фильма"


def test_prepare_images_movie_title_mode_without_provider_returns_none(monkeypatch):
    """image_providers_order пуст/провайдер недоступен (нет ключа) → пост без фото,
    не падаем."""
    result = _prepare_images(
        Mock(spec=LLMClient),
        raw_post_id=43,
        post_media_urls=[],
        rewritten_text="текст",
        images_config=_images_config(count_per_post=1),
        watermark_config=_watermark_config(),
        headline_card_config=HeadlineCardConfig(),
        image_providers={},  # google не сконфигурирован (нет GOOGLE_CSE_KEY)
        image_query_mode="movie_title",
        image_search_providers=["google"],
    )
    assert result is None


def test_prepare_images_movie_title_mode_no_title_extracted_returns_none(monkeypatch):
    """LLM не смог извлечь название фильма — пустой запрос, не ищем наугад."""
    import app.core.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "_safe_movie_query", lambda *a, **k: "")
    prepare_mock = Mock()
    monkeypatch.setattr(pipeline_module, "prepare_images_for_post", prepare_mock)

    result = _prepare_images(
        Mock(spec=LLMClient),
        raw_post_id=44,
        post_media_urls=[],
        rewritten_text="текст без названия",
        images_config=_images_config(count_per_post=1),
        watermark_config=_watermark_config(),
        headline_card_config=HeadlineCardConfig(),
        image_providers={"google": Mock()},
        image_query_mode="movie_title",
        image_search_providers=["google"],
    )

    assert result is None
    prepare_mock.assert_not_called()
