"""Кино не берёт посты, у которых текст на самом фото (ТЗ владельца 2026-08-30).

Кадры с плашкой/надписью выбрасываются поштучно; не осталось ни одного — пост
отклоняется целиком, и цикл идёт за следующим.
"""
from datetime import datetime, timezone
from unittest.mock import Mock, create_autospec

import app.core.pipeline as pipeline_module
from app.config.loader import FiltersConfig, RewriteConfig, ScoringWeights
from app.core.channel_settings import ChannelSettings
from app.core.llm.client import LLMClient
from app.core.monitoring.models import FetchedPost
from app.core.pipeline import _drop_text_photos, process_fetched_post
from app.db.repository import Repository, init_db, make_engine

WEIGHTS = ScoringWeights(
    news_value=0.35, keyword_match=0.25, source_views=0.20, freshness=0.10, source_priority=0.10
)
REWRITE_CONFIG = RewriteConfig(style="viral", max_length_chars=900, headline_variants=3)


def make_repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    return Repository(engine)


def make_filters() -> FiltersConfig:
    return FiltersConfig(
        min_score=75,
        important_score_threshold=88,
        duplicate_similarity_threshold=0.85,
        min_views=500,
        stop_words=[],
        required_keywords_boost=True,
        whitelist_keywords=[],
        blacklist_keywords=[],
    )


def make_post(**overrides) -> FetchedPost:
    defaults = dict(
        external_id="1",
        text="Вышел новый фильм про рыбака и русалку, премьера в декабре",
        post_type="photo",
        views=1000,
        published_at=datetime.now(timezone.utc),
        has_media=True,
    )
    defaults.update(overrides)
    return FetchedPost(**defaults)


def run_pipeline(repo, source, post, *, text_frames: set[str], skip_text_photos: bool):
    """Прогон кино-поста (фильтры off) с подменённым vision-детектором текста.

    Возвращает (outcome, rewrite_spy) — по вызовам рерайта видно, потратил ли
    отклонённый пост дорогие вызовы LLM."""
    client = Mock(spec=LLMClient)
    client.load_prompt.side_effect = lambda name: f"<{name}>"

    original_rewrite = pipeline_module.rewrite_post
    original_headlines = pipeline_module.generate_headlines
    original_detector = pipeline_module.image_has_heavy_text
    rewrite_spy = create_autospec(original_rewrite, return_value="Рерайт описания фильма")
    pipeline_module.rewrite_post = rewrite_spy
    pipeline_module.generate_headlines = Mock(return_value=["Русалка вернулась"])
    pipeline_module.image_has_heavy_text = lambda _client, path: str(path) in text_frames

    try:
        outcome = process_fetched_post(
            repo,
            source,
            post,
            llm_client=client,
            filters=make_filters(),
            scoring_weights=WEIGHTS,
            rewrite_config=REWRITE_CONFIG,
            max_post_age_hours=24,
            filters_enabled=False,
            skip_text_photos=skip_text_photos,
        )
    finally:
        pipeline_module.rewrite_post = original_rewrite
        pipeline_module.generate_headlines = original_headlines
        pipeline_module.image_has_heavy_text = original_detector

    return outcome, rewrite_spy


def make_source(repo):
    channel = repo.create_channel(name="Кино")
    return repo.create_source(type="vk", name="Кинопремьеры", url="58170807", channel_id=channel.id)


def test_single_photo_with_text_rejects_whole_post(tmp_path):
    """Одно фото и на нём плашка → пост не берём вовсе, ищем следующий."""
    repo = make_repo(tmp_path)
    source = make_source(repo)

    outcome, _ = run_pipeline(
        repo, source,
        make_post(external_id="10", media_urls=["/tmp/kino_10_0.jpg"]),
        text_frames={"/tmp/kino_10_0.jpg"},
        skip_text_photos=True,
    )

    assert outcome is not None
    assert outcome.accepted is None
    assert outcome.rejected is not None
    assert outcome.rejected.reason == "текст на фото"


def test_rejected_post_does_not_spend_llm_rewrite(tmp_path):
    """Проверка стоит ДО рерайта: отклонённый пост не жжёт лимит LLM."""
    repo = make_repo(tmp_path)
    source = make_source(repo)

    _, rewrite_spy = run_pipeline(
        repo, source,
        make_post(external_id="11", media_urls=["/tmp/kino_11_0.jpg"]),
        text_frames={"/tmp/kino_11_0.jpg"},
        skip_text_photos=True,
    )

    rewrite_spy.assert_not_called()


def test_post_survives_when_at_least_one_frame_is_clean(tmp_path):
    """Три фото, текст на последнем → пост берём, кадр с текстом выбрасываем."""
    repo = make_repo(tmp_path)
    source = make_source(repo)
    frames = ["/tmp/kino_12_0.jpg", "/tmp/kino_12_1.jpg", "/tmp/kino_12_2.jpg"]

    outcome, _ = run_pipeline(
        repo, source,
        make_post(external_id="12", media_urls=frames),
        text_frames={frames[2]},
        skip_text_photos=True,
    )

    assert outcome is not None
    assert outcome.rejected is None
    assert outcome.accepted is not None


def test_gate_off_keeps_post_with_text_photo(tmp_path):
    """Канал без настройки (Новости) ведёт себя ровно как раньше."""
    repo = make_repo(tmp_path)
    source = make_source(repo)

    outcome, _ = run_pipeline(
        repo, source,
        make_post(external_id="13", media_urls=["/tmp/kino_13_0.jpg"]),
        text_frames={"/tmp/kino_13_0.jpg"},
        skip_text_photos=False,
    )

    assert outcome is not None
    assert outcome.rejected is None


def test_drop_text_photos_stops_after_enough_clean_frames():
    """Vision зовётся не на все кадры подряд: набрали достаточно чистых — хватит."""
    checked: list[str] = []

    def detector(_client, path):
        checked.append(str(path))
        return False

    original = pipeline_module.image_has_heavy_text
    pipeline_module.image_has_heavy_text = detector
    try:
        frames = [f"/tmp/frame_{index}.jpg" for index in range(10)]
        kept = _drop_text_photos(Mock(spec=LLMClient), frames)
    finally:
        pipeline_module.image_has_heavy_text = original

    assert len(kept) == pipeline_module.TEXT_GATE_ENOUGH_FRAMES
    assert len(checked) == pipeline_module.TEXT_GATE_ENOUGH_FRAMES


def test_setting_survives_json_roundtrip():
    restored = ChannelSettings.from_json(ChannelSettings(skip_text_photos=True).to_json())
    assert restored.skip_text_photos is True
    assert ChannelSettings.from_json("{}").skip_text_photos is False
