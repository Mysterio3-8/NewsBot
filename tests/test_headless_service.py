from unittest.mock import AsyncMock, Mock

import pytest

from app.core.publishing.telegram_publisher import PublishResult, TelegramPublisher
from app.db.repository import Repository, init_db, make_engine
from app.headless_service import build_check_job, build_publish_job


def make_repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    return Repository(engine)


class FakeScheduleConfig:
    max_posts_per_day = 12


class FakeTelegramConfig:
    destination = "@channel"


class FakePublishingConfig:
    schedule = FakeScheduleConfig()
    telegram = FakeTelegramConfig()


class FakeFooterConfig:
    enabled = False
    label = "x"
    telegram_url = ""
    vk_url = ""


class FakeFiltersConfig:
    important_score_threshold = 88


class FakeAppConfig:
    publishing = FakePublishingConfig()
    footer = FakeFooterConfig()
    filters = FakeFiltersConfig()


@pytest.mark.asyncio
async def test_check_job_calls_run_check_cycle(tmp_path, monkeypatch):
    import app.headless_service as headless_service_module

    repo = make_repo(tmp_path)
    llm_client = Mock()
    tg_fetcher = Mock()
    vk_fetcher = Mock()
    config = FakeAppConfig()

    mock_run_check_cycle = AsyncMock()
    monkeypatch.setattr(headless_service_module, "run_check_cycle", mock_run_check_cycle)

    job = build_check_job(repo, config, llm_client, tg_fetcher, vk_fetcher)
    await job()

    mock_run_check_cycle.assert_awaited_once_with(
        repo, config, llm_client, tg_fetcher=tg_fetcher, vk_fetcher=vk_fetcher
    )


@pytest.mark.asyncio
async def test_publish_job_does_nothing_when_publisher_missing(tmp_path):
    repo = make_repo(tmp_path)
    config = FakeAppConfig()

    job = build_publish_job(repo, config, publisher=None)
    await job()  # не должно упасть


@pytest.mark.asyncio
async def test_publish_job_publishes_highest_score_queued_post(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    processed = repo.create_processed_post(raw_post_id=raw_post.id, score=90, status="queued")
    config = FakeAppConfig()

    publisher = AsyncMock(spec=TelegramPublisher)
    publisher.publish.return_value = PublishResult(success=True, message_id=1, error=None)

    job = build_publish_job(repo, config, publisher)
    await job()

    assert repo.get_processed_post(processed.id).status == "published"


@pytest.mark.asyncio
async def test_publish_job_does_nothing_when_queue_empty(tmp_path):
    repo = make_repo(tmp_path)
    config = FakeAppConfig()
    publisher = AsyncMock(spec=TelegramPublisher)

    job = build_publish_job(repo, config, publisher)
    await job()

    publisher.publish.assert_not_called()
