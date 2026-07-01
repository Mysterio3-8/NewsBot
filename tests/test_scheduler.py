from unittest.mock import AsyncMock

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config.loader import ScheduleConfig
from app.core.scheduler import PublishingScheduler, build_triggers, pick_next_post_to_publish
from app.db.repository import Repository, init_db, make_engine


def make_repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    return Repository(engine)


def test_build_triggers_fixed_slots_returns_cron_per_slot():
    schedule = ScheduleConfig(
        mode="fixed_slots", fixed_slots=["09:00", "13:30"], interval_minutes=40, max_posts_per_day=12
    )
    triggers = build_triggers(schedule)
    assert len(triggers) == 2
    assert all(isinstance(t, CronTrigger) for t in triggers)


def test_build_triggers_interval_mode_returns_single_trigger():
    schedule = ScheduleConfig(
        mode="interval", fixed_slots=[], interval_minutes=40, max_posts_per_day=12
    )
    triggers = build_triggers(schedule)
    assert len(triggers) == 1
    assert isinstance(triggers[0], IntervalTrigger)


def test_build_triggers_raises_on_unknown_mode():
    schedule = ScheduleConfig(
        mode="weird", fixed_slots=[], interval_minutes=40, max_posts_per_day=12
    )
    with pytest.raises(ValueError):
        build_triggers(schedule)


def test_pick_next_post_returns_none_when_queue_empty(tmp_path):
    repo = make_repo(tmp_path)
    assert pick_next_post_to_publish(repo, max_posts_per_day=12) is None


def test_pick_next_post_returns_highest_score(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    raw_a = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="a")
    raw_b = repo.create_raw_post(source_id=source.id, external_id="2", raw_text="b")
    repo.create_processed_post(raw_post_id=raw_a.id, score=50, status="queued")
    best = repo.create_processed_post(raw_post_id=raw_b.id, score=90, status="queued")

    picked = pick_next_post_to_publish(repo, max_posts_per_day=12)

    assert picked.id == best.id


def test_pick_next_post_returns_none_when_daily_limit_reached(tmp_path):
    import datetime

    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    raw_a = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="a")
    published = repo.create_processed_post(raw_post_id=raw_a.id, score=90, status="queued")
    repo.update_processed_post_status(
        published.id, "published", published_at=datetime.datetime.now(datetime.timezone.utc)
    )

    picked = pick_next_post_to_publish(repo, max_posts_per_day=1)

    assert picked is None


@pytest.mark.asyncio
async def test_publishing_scheduler_calls_on_slot_and_swallows_errors():
    schedule = ScheduleConfig(
        mode="interval", fixed_slots=[], interval_minutes=40, max_posts_per_day=12
    )
    on_slot = AsyncMock(side_effect=RuntimeError("падение задачи"))
    scheduler = PublishingScheduler(schedule, on_slot)

    await scheduler._run_slot()  # не должно поднять исключение наружу

    on_slot.assert_awaited_once()
