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
    publish_freshness_hours = 6


class FakeTelegramConfig:
    enabled = True
    token_env = "TG_BOT_TOKEN"
    destination = "@channel"


class FakeVKConfig:
    enabled = True
    token_env = "VK_GROUP_TOKEN"
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


class FakeAntibanConfig:
    # 0 = отключить жёсткий кулдаун в тестах (иначе TokenBucket(1/600) реально ждал бы
    # минуты между вызовами — asyncio.sleep пропатчен, но TokenBucket использует
    # time.sleep напрямую, не await).
    vk_personal_token_cooldown_seconds = 0


class FakeAppConfig:
    publishing = FakePublishingConfig()
    footer = FakeFooterConfig()
    filters = FakeFiltersConfig()
    rewrite = FakeRewriteConfig()
    monitoring = FakeMonitoringConfig()
    antiban = FakeAntibanConfig()


def _patch_env(monkeypatch):
    import app.headless_service as m

    monkeypatch.setattr(m, "build_image_providers", lambda: {})
    monkeypatch.setattr(m, "run_check_cycle", AsyncMock())
    # Пауза перед VK — реальная в проде (антибан), но в тестах не должна ждать минуты.
    monkeypatch.setattr(m.asyncio, "sleep", AsyncMock())
    return m


def _add_channel_post(
    repo, *, name="Новости", tg_dest="@channel", vk_dest="233689032",
    vk_token_env="VK_GROUP_TOKEN", src_url="-123", external_id="1",
):
    channel = repo.create_channel(
        name=name, tg_destination=tg_dest, vk_destination=vk_dest, vk_token_env=vk_token_env
    )
    source = repo.create_source(type="vk", name="Группа", url=src_url, channel_id=channel.id)
    raw_post = repo.create_raw_post(
        source_id=source.id, external_id=external_id, raw_text="новость"
    )
    processed = repo.create_processed_post(
        raw_post_id=raw_post.id, score=90, rewritten_text="Текст", status="queued"
    )
    return channel, processed


def _mock_publishers(monkeypatch, module, *, tg=None, vk=None):
    if tg is not None:
        monkeypatch.setattr(module, "build_telegram_publisher_for_channel", lambda ch: tg)
    if vk is not None:
        monkeypatch.setattr(
            module, "build_vk_publisher_for_channel", lambda ch, **kwargs: vk
        )


@pytest.mark.asyncio
async def test_cycle_job_runs_check_then_publishes_to_channel_targets(tmp_path, monkeypatch):
    m = _patch_env(monkeypatch)
    repo = make_repo(tmp_path)
    _, processed = _add_channel_post(repo)
    config = FakeAppConfig()

    tg_publisher = AsyncMock(spec=TelegramPublisher)
    tg_publisher.publish.return_value = PublishResult(success=True, message_id=1, error=None)
    vk_publisher = Mock(spec=VKPublisher)
    vk_publisher.publish.return_value = VKPublishResult(success=True, post_id=1, error=None)
    _mock_publishers(monkeypatch, m, tg=tg_publisher, vk=vk_publisher)

    job = build_cycle_job(repo, config, Mock(), tg_fetcher=Mock(), vk_fetcher=Mock())
    await job()

    tg_publisher.publish.assert_awaited_once()
    vk_publisher.publish.assert_called_once()
    assert vk_publisher.publish.call_args.kwargs["group_id"] == 233689032
    assert repo.get_processed_post(processed.id).status == "published"


@pytest.mark.asyncio
async def test_cycle_job_publishes_each_channel_to_its_own_vk_group(tmp_path, monkeypatch):
    """Мультиканальность: два канала публикуют в РАЗНЫЕ VK-группы своими таргетами."""
    m = _patch_env(monkeypatch)
    repo = make_repo(tmp_path)
    _add_channel_post(repo, name="Кино", tg_dest=None, vk_dest="240120678", src_url="-1", external_id="1")
    _add_channel_post(repo, name="Мемы", tg_dest=None, vk_dest="240120655", src_url="-2", external_id="2")
    config = FakeAppConfig()

    vk_publisher = Mock(spec=VKPublisher)
    vk_publisher.publish.return_value = VKPublishResult(success=True, post_id=1, error=None)
    _mock_publishers(monkeypatch, m, tg=None, vk=vk_publisher)

    job = build_cycle_job(repo, config, Mock(), tg_fetcher=Mock(), vk_fetcher=Mock())
    await job()

    group_ids = {call.kwargs["group_id"] for call in vk_publisher.publish.call_args_list}
    assert group_ids == {240120678, 240120655}


@pytest.mark.asyncio
async def test_cycle_job_skips_tg_when_channel_has_no_tg_destination(tmp_path, monkeypatch):
    """Кино/мемы пока только VK (tg_destination пуст) — в TG не постим, даже если бот есть."""
    m = _patch_env(monkeypatch)
    repo = make_repo(tmp_path)
    _add_channel_post(repo, name="Кино", tg_dest=None, vk_dest="240120678")
    config = FakeAppConfig()

    tg_publisher = AsyncMock(spec=TelegramPublisher)
    vk_publisher = Mock(spec=VKPublisher)
    vk_publisher.publish.return_value = VKPublishResult(success=True, post_id=1, error=None)
    _mock_publishers(monkeypatch, m, tg=tg_publisher, vk=vk_publisher)

    job = build_cycle_job(repo, config, Mock(), tg_fetcher=Mock(), vk_fetcher=Mock())
    await job()

    tg_publisher.publish.assert_not_called()
    vk_publisher.publish.assert_called_once()


@pytest.mark.asyncio
async def test_cycle_job_does_nothing_when_queue_empty(tmp_path, monkeypatch):
    m = _patch_env(monkeypatch)
    repo = make_repo(tmp_path)
    repo.create_channel(name="Новости", tg_destination="@channel", vk_destination="233689032")
    config = FakeAppConfig()

    tg_publisher = AsyncMock(spec=TelegramPublisher)
    vk_publisher = Mock(spec=VKPublisher)
    _mock_publishers(monkeypatch, m, tg=tg_publisher, vk=vk_publisher)

    job = build_cycle_job(repo, config, Mock(), tg_fetcher=Mock(), vk_fetcher=Mock())
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
    m = _patch_env(monkeypatch)
    repo = make_repo(tmp_path)
    _add_channel_post(repo)
    config = FakeAppConfig()

    tg_publisher = AsyncMock(spec=TelegramPublisher)
    tg_publisher.publish.side_effect = RuntimeError("сеть")
    vk_publisher = Mock(spec=VKPublisher)
    vk_publisher.publish.return_value = VKPublishResult(success=True, post_id=1, error=None)
    _mock_publishers(monkeypatch, m, tg=tg_publisher, vk=vk_publisher)

    job = build_cycle_job(repo, config, Mock(), tg_fetcher=Mock(), vk_fetcher=Mock())
    await job()

    vk_publisher.publish.assert_called_once()


@pytest.mark.asyncio
async def test_cycle_job_holds_whole_post_when_vk_breaker_open(tmp_path, monkeypatch):
    """ЖЁСТКАЯ ПАРА (ТЗ 2026-07-27): если VK-сеть цель, но её breaker открыт, в TG в
    одиночку НЕ публикуем — весь пост откладываем, чтобы сети не расходились. Пост
    остаётся в очереди и уйдёт в ОБЕ сети, когда VK снова будет доступен."""
    from app.core.publishing.circuit_breaker import CircuitBreaker
    from app.core.publishing.vk_errors import VKErrorClass

    m = _patch_env(monkeypatch)
    repo = make_repo(tmp_path)
    _add_channel_post(repo, vk_token_env="VK_GROUP_TOKEN")
    CircuitBreaker(repo).record_failure("vk", "VK_GROUP_TOKEN", VKErrorClass.RATE_LIMIT)
    config = FakeAppConfig()

    tg_publisher = AsyncMock(spec=TelegramPublisher)
    tg_publisher.publish.return_value = PublishResult(success=True, message_id=1, error=None)
    vk_publisher = Mock(spec=VKPublisher)
    _mock_publishers(monkeypatch, m, tg=tg_publisher, vk=vk_publisher)

    job = build_cycle_job(repo, config, Mock(), tg_fetcher=Mock(), vk_fetcher=Mock())
    await job()

    tg_publisher.publish.assert_not_awaited()  # в TG в одиночку не публикуем — ждём VK
    vk_publisher.publish.assert_not_called()
    assert repo.list_processed_posts(status="queued")  # пост остался в очереди


@pytest.mark.asyncio
async def test_cycle_job_returns_post_to_queue_when_vk_fails_after_tg(tmp_path, monkeypatch):
    """Пара не закрылась: TG прошёл, VK упал — пост возвращается в очередь (не остаётся
    навсегда в TG-only), чтобы VK догнать законным кросс-постом в следующем цикле."""
    m = _patch_env(monkeypatch)
    repo = make_repo(tmp_path)
    _, processed = _add_channel_post(repo)
    config = FakeAppConfig()

    tg_publisher = AsyncMock(spec=TelegramPublisher)
    tg_publisher.publish.return_value = PublishResult(success=True, message_id=1, error=None)
    vk_publisher = Mock(spec=VKPublisher)
    vk_publisher.publish.return_value = VKPublishResult(success=False, post_id=None, error="boom")
    _mock_publishers(monkeypatch, m, tg=tg_publisher, vk=vk_publisher)

    job = build_cycle_job(repo, config, Mock(), tg_fetcher=Mock(), vk_fetcher=Mock())
    await job()

    tg_publisher.publish.assert_awaited_once()
    vk_publisher.publish.assert_called_once()
    updated = repo.get_processed_post(processed.id)
    assert updated.status == "queued"  # пара не закрыта → снова в очередь
    assert repo.get_published_network_at(processed.id, "tg") is not None
    assert repo.get_published_network_at(processed.id, "vk") is None


@pytest.mark.asyncio
async def test_cycle_job_skips_disabled_network(tmp_path, monkeypatch):
    m = _patch_env(monkeypatch)
    repo = make_repo(tmp_path)
    _add_channel_post(repo)

    config = FakeAppConfig()
    config.publishing.vk.enabled = False

    tg_publisher = AsyncMock(spec=TelegramPublisher)
    tg_publisher.publish.return_value = PublishResult(success=True, message_id=1, error=None)
    vk_publisher = Mock(spec=VKPublisher)
    _mock_publishers(monkeypatch, m, tg=tg_publisher, vk=vk_publisher)

    job = build_cycle_job(repo, config, Mock(), tg_fetcher=Mock(), vk_fetcher=Mock())
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
            # add_job вызывается несколько раз (цикл + недельный репост); нас интересует
            # первый — цикловой джоб, у которого должен быть немедленный next_run_time.
            captured.setdefault("next_run_time", next_run_time)

        def start(self):
            pass

        def shutdown(self, wait=False):
            pass

    monkeypatch.setattr(headless_module, "AsyncIOScheduler", FakeScheduler)
    monkeypatch.setattr(headless_module, "build_telegram_fetcher", lambda: None)
    monkeypatch.setattr(headless_module, "build_vk_fetcher", lambda **kwargs: None)

    repo = make_repo(tmp_path)
    config = FakeAppConfig()
    stop_event = asyncio.Event()
    stop_event.set()  # завершить run_forever сразу после старта планировщика

    await run_forever(repo, config, Mock(), stop_event=stop_event)

    assert captured["next_run_time"] is not None


def test_sync_default_channel_targets_fills_from_config(tmp_path):
    """Канал 1 (Новости) создан миграцией с пустыми таргетами — run_forever заполняет
    их из глобального config, чтобы новости постили как раньше."""
    from app.headless_service import _sync_default_channel_targets

    repo = make_repo(tmp_path)
    # «Новости» с пустыми таргетами — как после миграции _ensure_default_channel
    default = repo.create_channel(name="Новости")

    _sync_default_channel_targets(repo, FakeAppConfig())

    updated = repo.get_channel(default.id)
    assert updated.tg_destination == "@channel"
    assert updated.vk_destination == "233689032"
