"""Канал «Infinity Music — новость дня»: одна музыкальная новость в сутки, только VK.

ТЗ владельца 2026-09-05: «плюс хочу ещё добавить немного новостей, я дам канал откуда
брать… 1 новость», источник https://t.me/fastfoodmusictg, «публиковать только в вк
https://vk.ru/infinitymusicplayer».
"""
from datetime import datetime, timezone
from unittest.mock import Mock, create_autospec

import app.core.pipeline as pipeline_module
from app.config.loader import FiltersConfig, FooterConfig, RewriteConfig, ScoringWeights
from app.core.channel_settings import ChannelSettings
from app.core.llm.client import LLMClient
from app.core.maintenance.heartbeat import DEFAULT_WATCHLIST
from app.core.monitoring.models import FetchedPost
from app.core.pipeline import process_fetched_post
from app.core.publishing.footer import build_channel_footer, build_vk_footer
from app.db.repository import Repository, init_db, make_engine
from app.seed_channels import (
    MUSIC_NEWS_SETTINGS,
    MUSIC_NEWS_SOURCE_URL,
    MUSIC_NEWS_VK_GROUP,
    seed_music_news,
)

WEIGHTS = ScoringWeights(
    news_value=0.35, keyword_match=0.25, source_views=0.20, freshness=0.10, source_priority=0.10
)


def make_repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    return Repository(engine)


def test_seed_creates_vk_only_channel_with_the_owners_source(tmp_path):
    repo = make_repo(tmp_path)

    seed_music_news(repo)

    channel = next(c for c in repo.list_channels() if c.vk_destination == MUSIC_NEWS_VK_GROUP)
    assert channel.tg_destination is None, "владелец просил публиковать только в VK"
    assert channel.vk_token_env == "VK_GROUP_TOKEN_MUSIC"
    assert [s.url for s in repo.list_sources(channel_id=channel.id)] == [MUSIC_NEWS_SOURCE_URL]


def test_seed_is_idempotent(tmp_path):
    """Повторный прогон сида не плодит ни канал, ни источник."""
    repo = make_repo(tmp_path)

    seed_music_news(repo)
    seed_music_news(repo)

    channels = [c for c in repo.list_channels() if c.vk_destination == MUSIC_NEWS_VK_GROUP]
    assert len(channels) == 1
    assert len(repo.list_sources(channel_id=channels[0].id)) == 1


def test_seeded_channel_stays_disabled_until_its_token_exists(tmp_path):
    """Без VK_GROUP_TOKEN_MUSIC включённый канал только сыпал бы ошибками в лог."""
    repo = make_repo(tmp_path)

    seed_music_news(repo)

    channel = next(c for c in repo.list_channels() if c.vk_destination == MUSIC_NEWS_VK_GROUP)
    assert not channel.enabled


def test_one_news_per_day():
    settings = ChannelSettings(**MUSIC_NEWS_SETTINGS)
    assert settings.max_posts_per_day == 1


def test_vk_group_matches_the_silence_watchdog():
    """id сообщества не выдуман: сторож тишины следит за этой же стеной."""
    music = next(c for c in DEFAULT_WATCHLIST if c.name == "Infinity Music")
    assert str(music.group_id) == MUSIC_NEWS_VK_GROUP


def make_post(text: str) -> FetchedPost:
    return FetchedPost(
        external_id="1",
        text=text,
        post_type="text",
        views=1000,
        published_at=datetime.now(timezone.utc),
        has_media=False,
    )


def run_channel_pipeline(repo, text: str):
    source = repo.create_source(type="tg", name="Fastfood Music", url=MUSIC_NEWS_SOURCE_URL)
    client = Mock(spec=LLMClient)
    client.load_prompt.side_effect = lambda name: f"<{name}>"

    original_rewrite = pipeline_module.rewrite_post
    original_headlines = pipeline_module.generate_headlines
    pipeline_module.rewrite_post = create_autospec(original_rewrite, return_value="Рерайт")
    pipeline_module.generate_headlines = Mock(return_value=["Хук"])
    try:
        return process_fetched_post(
            repo,
            source,
            make_post(text),
            llm_client=client,
            filters=FiltersConfig(
                min_score=75, important_score_threshold=88, duplicate_similarity_threshold=0.85,
                min_views=500, stop_words=[], required_keywords_boost=True,
                whitelist_keywords=[], blacklist_keywords=[],
            ),
            scoring_weights=WEIGHTS,
            rewrite_config=RewriteConfig(style="viral", max_length_chars=900, headline_variants=3),
            max_post_age_hours=24,
            filters_enabled=MUSIC_NEWS_SETTINGS["filters_enabled"],
            channel_stop_words=MUSIC_NEWS_SETTINGS["stop_words"],
        )
    finally:
        pipeline_module.rewrite_post = original_rewrite
        pipeline_module.generate_headlines = original_headlines


def test_channel_stop_words_work_in_pour_everything_mode(tmp_path):
    """ТЗ владельца 2026-09-02: СВО/ВСУ/ЗСУ не брать. Глобальные стоп-слова этот канал
    не видит вовсе — он живёт в режиме «лить всё»."""
    outcome = run_channel_pipeline(make_repo(tmp_path), "Новый трек про ВСУ вышел сегодня")

    assert outcome is not None
    assert outcome.accepted is None
    assert outcome.rejected.reason.startswith("стоп-слово канала")


def test_ordinary_music_news_passes(tmp_path):
    outcome = run_channel_pipeline(make_repo(tmp_path), "Артист выпустил новый альбом")

    assert outcome is not None
    assert outcome.rejected is None


def test_vk_footer_speaks_about_listening_not_subscribing():
    """«Подписывайтесь на Telegram-канал» под музыкальным постом звучит чужеродно."""
    settings = ChannelSettings(**MUSIC_NEWS_SETTINGS)
    links = build_channel_footer(
        settings.tg_footer_url,
        settings.tg_footer_signature,
        FooterConfig(
            enabled=False, label="", telegram_url="", vk_url="",
            telegram_signature="Новости", subscribe_cta="Подписывайтесь на Telegram-канал:",
        ),
        None,
        vk_cta=settings.vk_footer_cta,
    )

    footer = build_vk_footer(links)

    assert footer.startswith("Слушать и скачивать — в нашем Telegram:")
    assert "https://t.me/muz_damn_bot" in footer
