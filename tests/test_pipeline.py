import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from PIL import Image

from app.config.loader import FiltersConfig, RewriteConfig, ScoringWeights, WatermarkConfig
from app.core.llm.classifier import ClassificationError, ClassificationResult
from app.core.llm.client import LLMClient
from app.core.pipeline import _filter_watermarked_photos, process_fetched_post
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
    pipeline_module.rewrite_post = Mock(return_value="Переписанный текст новости")
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


def test_process_fetched_post_caps_rewrite_length_to_original_post_length(tmp_path):
    """По запросу пользователя: рерайт не должен быть длиннее исходного поста —
    max_length передаётся в rewrite_post как min(конфиг, длина оригинала), а не
    всегда фиксированный потолок из config.yaml (900 символов растягивал короткие
    новости искусственными подробностями)."""
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x", priority=8)
    client = Mock(spec=LLMClient)
    client.load_prompt.side_effect = lambda name: f"<{name}>"

    short_post = make_post(text="Госдума приняла закон.")  # короче 900, whitelist-слово для проходного скора

    import app.core.pipeline as pipeline_module

    original_classify = pipeline_module.classify_post
    original_rewrite = pipeline_module.rewrite_post
    original_headlines = pipeline_module.generate_headlines
    pipeline_module.classify_post = Mock(
        return_value=ClassificationResult(
            is_news=True, category="политика", score=90, reasons=["важно"], reject_reason=None
        )
    )
    rewrite_mock = Mock(return_value="Переписанный текст новости")
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
    assert kwargs["max_length"] == len("Госдума приняла закон.")


def test_filter_watermarked_photos_drops_local_files_with_detected_watermark(monkeypatch):
    """По запросу пользователя 2026-07-05: фото с чужим логотипом/подписью не
    должны идти в публикацию — пайплайн вместо них падает на сток-фолбэк."""
    import app.core.pipeline as pipeline_module

    client = Mock(spec=LLMClient)

    def fake_detect(_client, path):
        return str(path) == "watermarked.jpg"

    monkeypatch.setattr(pipeline_module, "detect_foreign_watermark", fake_detect)

    result = _filter_watermarked_photos(client, ["clean.jpg", "watermarked.jpg"])

    assert result == ["clean.jpg"]


def test_filter_watermarked_photos_keeps_remote_urls_unchecked(monkeypatch):
    """VK отдаёт прямые URL — на этом шаге локального файла для vision-анализа ещё
    нет, поэтому такие элементы проходят без проверки (не regression, просто scope)."""
    import app.core.pipeline as pipeline_module

    client = Mock(spec=LLMClient)
    detect_mock = Mock(return_value=True)
    monkeypatch.setattr(pipeline_module, "detect_foreign_watermark", detect_mock)

    result = _filter_watermarked_photos(client, ["https://vk.com/photo1.jpg"])

    assert result == ["https://vk.com/photo1.jpg"]
    detect_mock.assert_not_called()


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
    pipeline_module.rewrite_post = Mock(return_value="Переписанный текст новости")
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
            make_post(text="Успей купить со скидка!"),
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
