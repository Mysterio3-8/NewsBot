from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest

from app.config.loader import FiltersConfig, RewriteConfig, ScoringWeights
from app.core.check_cycle import run_check_cycle
from app.core.llm.classifier import ClassificationResult
from app.core.llm.client import LLMClient
from app.core.monitoring.models import FetchedPost
from app.db.repository import Repository, init_db, make_engine

WEIGHTS = ScoringWeights(
    news_value=0.35, keyword_match=0.25, source_views=0.20, freshness=0.10, source_priority=0.10
)


class FakeAppConfig:
    def __init__(self):
        self.monitoring = Mock(max_post_age_hours=24)
        self.filters = FiltersConfig(
            min_score=75,
            important_score_threshold=88,
            duplicate_similarity_threshold=0.85,
            min_views=500,
            stop_words=[],
            required_keywords_boost=True,
            whitelist_keywords=["Госдума"],
            blacklist_keywords=[],
        )
        self.scoring_weights = WEIGHTS
        self.rewrite = RewriteConfig(style="viral", max_length_chars=900, headline_variants=3)
        self.images = Mock()
        self.watermark = Mock()


def make_repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    return Repository(engine)


def make_post(external_id: str) -> FetchedPost:
    return FetchedPost(
        external_id=external_id,
        text="Госдума приняла новый закон о бюджете",
        post_type="text",
        views=1000,
        published_at=datetime.now(timezone.utc),
        has_media=False,
    )


@pytest.mark.asyncio
async def test_run_check_cycle_processes_telegram_sources(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    repo.create_source(type="tg", name="Канал", url="https://t.me/x", priority=8)
    config = FakeAppConfig()
    client = Mock(spec=LLMClient)

    tg_fetcher = Mock()
    tg_fetcher.fetch_recent_posts = AsyncMock(return_value=[make_post("1")])

    import app.core.check_cycle as check_cycle_module

    monkeypatch.setattr(
        check_cycle_module,
        "process_fetched_post",
        Mock(return_value=None),
    )

    await run_check_cycle(repo, config, client, tg_fetcher=tg_fetcher, vk_fetcher=None)

    tg_fetcher.fetch_recent_posts.assert_awaited_once_with(
        "https://t.me/x", max_age_hours=24, known_external_ids=set()
    )
    check_cycle_module.process_fetched_post.assert_called_once()


@pytest.mark.asyncio
async def test_run_check_cycle_skips_disabled_sources(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    repo.update_source(source.id, enabled=False)
    config = FakeAppConfig()
    client = Mock(spec=LLMClient)

    tg_fetcher = Mock()
    tg_fetcher.fetch_recent_posts = AsyncMock(return_value=[make_post("1")])

    await run_check_cycle(repo, config, client, tg_fetcher=tg_fetcher, vk_fetcher=None)

    tg_fetcher.fetch_recent_posts.assert_not_called()


@pytest.mark.asyncio
async def test_run_check_cycle_processes_vk_sources_using_numeric_url(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    repo.create_source(type="vk", name="Группа", url="12345", priority=5)
    config = FakeAppConfig()
    client = Mock(spec=LLMClient)

    vk_fetcher = Mock()
    vk_fetcher.fetch_recent_posts = Mock(return_value=[make_post("1")])

    import app.core.check_cycle as check_cycle_module

    monkeypatch.setattr(check_cycle_module, "process_fetched_post", Mock(return_value=None))

    await run_check_cycle(repo, config, client, tg_fetcher=None, vk_fetcher=vk_fetcher)

    vk_fetcher.fetch_recent_posts.assert_called_once_with(12345, max_age_hours=24)


@pytest.mark.asyncio
async def test_run_check_cycle_continues_when_vk_source_fetch_raises(tmp_path, monkeypatch):
    """Регрессия: на проде забаненный VK_USER_TOKEN приводил к необработанному
    исключению в fetch_recent_posts, которое рушило весь run_check_cycle — из-за
    этого headless-цикл никогда не доходил до шага публикации (та идёт ПОСЛЕ
    run_check_cycle в headless_service.cycle_job), и публикации молчали часами
    при полной очереди. Один сломанный источник не должен рушить остальные."""
    repo = make_repo(tmp_path)
    repo.create_source(type="tg", name="Канал", url="https://t.me/x", priority=8)
    repo.create_source(type="vk", name="Группа", url="12345", priority=5)
    config = FakeAppConfig()
    client = Mock(spec=LLMClient)

    tg_fetcher = Mock()
    tg_fetcher.fetch_recent_posts = AsyncMock(return_value=[make_post("1")])
    vk_fetcher = Mock()
    vk_fetcher.fetch_recent_posts = Mock(side_effect=RuntimeError("[5] User authorization failed: user is blocked"))

    import app.core.check_cycle as check_cycle_module

    monkeypatch.setattr(check_cycle_module, "process_fetched_post", Mock(return_value=None))

    # Не должно поднять исключение наружу — цикл должен доработать до конца.
    await run_check_cycle(repo, config, client, tg_fetcher=tg_fetcher, vk_fetcher=vk_fetcher)

    tg_fetcher.fetch_recent_posts.assert_awaited_once()
    check_cycle_module.process_fetched_post.assert_called_once()


@pytest.mark.asyncio
async def test_run_check_cycle_continues_when_tg_source_fetch_raises(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    repo.create_source(type="tg", name="Сломанный", url="https://t.me/broken", priority=8)
    repo.create_source(type="tg", name="Рабочий", url="https://t.me/ok", priority=5)
    config = FakeAppConfig()
    client = Mock(spec=LLMClient)

    tg_fetcher = Mock()
    tg_fetcher.fetch_recent_posts = AsyncMock(
        side_effect=[RuntimeError("сеть недоступна"), [make_post("1")]]
    )

    import app.core.check_cycle as check_cycle_module

    monkeypatch.setattr(check_cycle_module, "process_fetched_post", Mock(return_value=None))

    await run_check_cycle(repo, config, client, tg_fetcher=tg_fetcher, vk_fetcher=None)

    assert tg_fetcher.fetch_recent_posts.await_count == 2
    check_cycle_module.process_fetched_post.assert_called_once()


@pytest.mark.asyncio
async def test_run_check_cycle_continues_after_single_post_error(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    config = FakeAppConfig()
    client = Mock(spec=LLMClient)

    tg_fetcher = Mock()
    tg_fetcher.fetch_recent_posts = AsyncMock(return_value=[make_post("1"), make_post("2")])

    import app.core.check_cycle as check_cycle_module

    monkeypatch.setattr(
        check_cycle_module,
        "process_fetched_post",
        Mock(side_effect=[RuntimeError("бум"), None]),
    )

    await run_check_cycle(repo, config, client, tg_fetcher=tg_fetcher, vk_fetcher=None)

    assert check_cycle_module.process_fetched_post.call_count == 2
