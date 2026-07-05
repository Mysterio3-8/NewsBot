import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.publishing.telegram_publisher import PublishResult, TelegramPublisher
from app.core.publishing.vk_publisher import VKPublisher, VKPublishResult
from app.db.repository import Repository, init_db, make_engine
from app.headless_service import _explain_no_post_reason, build_cycle_job, run_forever


def make_repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    return Repository(engine)


class FakeScheduleConfig:
    max_posts_per_day = 12
    min_interval_minutes = 0  # в тестах стопор по интервалу не мешает
    jitter_minutes = 0


class FakeTelegramConfig:
    enabled = True
    destination = "@channel"


class FakeVKConfig:
    enabled = True
    destination = "233689032"


class FakePublishingConfig:
    schedule = FakeScheduleConfig()
    telegram = FakeTelegramConfig()
    vk = FakeVKConfig()


class FakeFooterConfig:
    enabled = False
    label = "x"
    telegram_url = ""
    vk_url = ""


class FakeFiltersConfig:
    important_score_threshold = 88


class FakeRewriteConfig:
    include_hashtags = False


class FakeMonitoringConfig:
    check_interval_minutes = 240


class FakeAppConfig:
    publishing = FakePublishingConfig()
    footer = FakeFooterConfig()
    filters = FakeFiltersConfig()
    rewrite = FakeRewriteConfig()
    monitoring = FakeMonitoringConfig()


def _patch_env(monkeypatch):
    import app.headless_service as m

    monkeypatch.setattr(m, "build_image_providers", lambda: {})
    monkeypatch.setattr(m, "run_check_cycle", AsyncMock())
    # Пауза перед VK — реальная в проде (антибан), но в тестах не должна ждать минуты.
    monkeypatch.setattr(m.asyncio, "sleep", AsyncMock())
    return m


@pytest.mark.asyncio
async def test_cycle_job_runs_check_then_publishes_to_both_networks(tmp_path, monkeypatch):
    _patch_env(monkeypatch)
    repo = make_repo(tmp_path)
    source = repo.create_source(type="vk", name="Группа", url="-123")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    processed = repo.create_processed_post(
        raw_post_id=raw_post.id, score=90, rewritten_text="Текст", status="queued"
    )
    config = FakeAppConfig()

    tg_publisher = AsyncMock(spec=TelegramPublisher)
    tg_publisher.publish.return_value = PublishResult(success=True, message_id=1, error=None)
    vk_publisher = Mock(spec=VKPublisher)
    vk_publisher.publish.return_value = VKPublishResult(success=True, post_id=1, error=None)

    job = build_cycle_job(
        repo,
        config,
        Mock(),
        tg_fetcher=Mock(),
        vk_fetcher=Mock(),
        tg_publisher=tg_publisher,
        vk_publisher=vk_publisher,
    )
    await job()

    tg_publisher.publish.assert_awaited_once()
    vk_publisher.publish.assert_called_once()
    assert repo.get_processed_post(processed.id).status == "published"


@pytest.mark.asyncio
async def test_cycle_job_does_nothing_when_queue_empty(tmp_path, monkeypatch):
    _patch_env(monkeypatch)
    repo = make_repo(tmp_path)
    config = FakeAppConfig()

    tg_publisher = AsyncMock(spec=TelegramPublisher)
    vk_publisher = Mock(spec=VKPublisher)

    job = build_cycle_job(
        repo,
        config,
        Mock(),
        tg_fetcher=Mock(),
        vk_fetcher=Mock(),
        tg_publisher=tg_publisher,
        vk_publisher=vk_publisher,
    )
    await job()

    tg_publisher.publish.assert_not_called()
    vk_publisher.publish.assert_not_called()


def test_explain_no_post_reason_reports_empty_queue(tmp_path):
    """Регрессия: раньше 'Нет постов к публикации' не объясняло, пуста ли очередь
    или упёрлись в дневной лимит — приходилось лезть в БД руками, чтобы понять."""
    repo = make_repo(tmp_path)
    reason = _explain_no_post_reason(repo, max_posts_per_day=12)
    assert "очередь пуста" in reason


def test_explain_no_post_reason_reports_daily_cap_reached(tmp_path):
    import datetime

    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    now = datetime.datetime.utcnow()
    for i in range(3):
        raw = repo.create_raw_post(source_id=source.id, external_id=f"p{i}", raw_text="x")
        p = repo.create_processed_post(raw_post_id=raw.id, score=90, status="queued")
        repo.update_processed_post_status(p.id, "published", published_at=now)

    reason = _explain_no_post_reason(repo, max_posts_per_day=3)
    assert "дневной лимит" in reason
    assert "3/3" in reason


def test_explain_no_post_reason_reports_queue_has_posts_but_none_picked(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    raw = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="x")
    repo.create_processed_post(raw_post_id=raw.id, score=90, status="queued")

    reason = _explain_no_post_reason(repo, max_posts_per_day=12)
    assert "в очереди 1 постов" in reason


@pytest.mark.asyncio
async def test_cycle_job_tg_failure_does_not_block_vk(tmp_path, monkeypatch):
    _patch_env(monkeypatch)
    repo = make_repo(tmp_path)
    source = repo.create_source(type="vk", name="Группа", url="-123")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    repo.create_processed_post(
        raw_post_id=raw_post.id, score=90, rewritten_text="Текст", status="queued"
    )
    config = FakeAppConfig()

    tg_publisher = AsyncMock(spec=TelegramPublisher)
    tg_publisher.publish.side_effect = RuntimeError("сеть")
    vk_publisher = Mock(spec=VKPublisher)
    vk_publisher.publish.return_value = VKPublishResult(success=True, post_id=1, error=None)

    job = build_cycle_job(
        repo,
        config,
        Mock(),
        tg_fetcher=Mock(),
        vk_fetcher=Mock(),
        tg_publisher=tg_publisher,
        vk_publisher=vk_publisher,
    )
    await job()

    vk_publisher.publish.assert_called_once()


@pytest.mark.asyncio
async def test_cycle_job_skips_disabled_network(tmp_path, monkeypatch):
    _patch_env(monkeypatch)
    repo = make_repo(tmp_path)
    source = repo.create_source(type="vk", name="Группа", url="-123")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    repo.create_processed_post(
        raw_post_id=raw_post.id, score=90, rewritten_text="Текст", status="queued"
    )

    config = FakeAppConfig()
    config.publishing.vk.enabled = False

    tg_publisher = AsyncMock(spec=TelegramPublisher)
    tg_publisher.publish.return_value = PublishResult(success=True, message_id=1, error=None)
    vk_publisher = Mock(spec=VKPublisher)

    job = build_cycle_job(
        repo,
        config,
        Mock(),
        tg_fetcher=Mock(),
        vk_fetcher=Mock(),
        tg_publisher=tg_publisher,
        vk_publisher=vk_publisher,
    )
    await job()

    tg_publisher.publish.assert_awaited_once()
    vk_publisher.publish.assert_not_called()
    config.publishing.vk.enabled = True  # восстановить для других тестов


@pytest.mark.asyncio
async def test_run_forever_schedules_immediate_first_cycle(tmp_path, monkeypatch):
    """Регрессия: IntervalTrigger без next_run_time ждёт первый ПОЛНЫЙ интервал
    (до 4 часов) прежде чем сделать хоть что-то — при нажатии "Старт" в веб-лаунчере
    пользователь ждал бы первую проверку часами. Первый цикл должен запускаться сразу."""
    import app.headless_service as headless_module

    captured = {}

    class FakeScheduler:
        def add_job(self, func, trigger, next_run_time=None):
            captured["next_run_time"] = next_run_time

        def start(self):
            pass

        def shutdown(self, wait=False):
            pass

    monkeypatch.setattr(headless_module, "AsyncIOScheduler", FakeScheduler)
    monkeypatch.setattr(headless_module, "build_telegram_fetcher", lambda: None)
    monkeypatch.setattr(headless_module, "build_vk_fetcher", lambda: None)
    monkeypatch.setattr(headless_module, "build_telegram_publisher", lambda config: None)
    monkeypatch.setattr(headless_module, "build_vk_publisher", lambda config: None)

    repo = make_repo(tmp_path)
    config = FakeAppConfig()
    stop_event = asyncio.Event()
    stop_event.set()  # завершить run_forever сразу после старта планировщика

    await run_forever(repo, config, Mock(), stop_event=stop_event)

    assert captured["next_run_time"] is not None
