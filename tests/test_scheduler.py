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


import datetime

from app.db.models import ProcessedPost


def _set_created_at(repo, post_id, when) -> None:
    with repo._session_factory() as session:
        session.query(ProcessedPost).filter(ProcessedPost.id == post_id).update(
            {"created_at": when}
        )
        session.commit()


def test_pick_next_post_returns_none_when_queue_empty(tmp_path):
    repo = make_repo(tmp_path)
    assert pick_next_post_to_publish(repo, max_posts_per_day=12, freshness_hours=6) is None


def test_pick_next_post_returns_freshest_not_highest_score(tmp_path):
    """Запрос пользователя 2026-07-05: только свежие новости — выбираем самый свежий
    пост (по created_at), а НЕ с самым высоким score (раньше публиковались старые)."""
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    raw_a = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="a")
    raw_b = repo.create_raw_post(source_id=source.id, external_id="2", raw_text="b")
    old_high = repo.create_processed_post(raw_post_id=raw_a.id, score=99, status="queued")
    fresh_low = repo.create_processed_post(raw_post_id=raw_b.id, score=40, status="queued")
    # старый пост с высоким score создан 3 часа назад, свежий — только что
    _set_created_at(repo, old_high.id, datetime.datetime.utcnow() - datetime.timedelta(hours=3))

    picked = pick_next_post_to_publish(repo, max_posts_per_day=12, freshness_hours=6)

    assert picked.id == fresh_low.id  # свежий, несмотря на низкий score


def test_pick_next_post_ignores_and_expires_stale_posts(tmp_path):
    """Пост старше freshness_hours не публикуется и помечается expired — чтобы старые
    новости (в т.ч. со старыми впечатанными подписями) никогда не всплывали."""
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    raw = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="a")
    stale = repo.create_processed_post(raw_post_id=raw.id, score=99, status="queued")
    _set_created_at(repo, stale.id, datetime.datetime.utcnow() - datetime.timedelta(hours=10))

    picked = pick_next_post_to_publish(repo, max_posts_per_day=12, freshness_hours=4)

    assert picked is None
    assert repo.get_processed_post(stale.id).status == "expired"


def test_pick_next_post_returns_none_when_daily_limit_reached(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    raw_a = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="a")
    published = repo.create_processed_post(raw_post_id=raw_a.id, score=90, status="queued")
    repo.update_processed_post_status(
        published.id, "published", published_at=datetime.datetime.now(datetime.timezone.utc)
    )

    picked = pick_next_post_to_publish(repo, max_posts_per_day=1, freshness_hours=6)

    assert picked is None


def test_pick_next_post_prefers_fresh_post_with_image_over_fresher_without(tmp_path):
    """Медиа на КАЖДОМ посте — среди свежих берём тот, у кого есть картинка, даже
    если чуть более свежий без картинки."""
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    raw_a = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="a")
    raw_b = repo.create_raw_post(source_id=source.id, external_id="2", raw_text="b")
    with_image = repo.create_processed_post(
        raw_post_id=raw_a.id, score=80, status="queued", image_paths=["output/images/1/photo.jpg"]
    )
    no_image = repo.create_processed_post(raw_post_id=raw_b.id, score=95, status="queued")
    # no_image свежее (создан позже), но картинки нет — берём with_image
    _set_created_at(repo, with_image.id, datetime.datetime.utcnow() - datetime.timedelta(minutes=10))

    picked = pick_next_post_to_publish(repo, max_posts_per_day=12, freshness_hours=6)

    assert picked.id == with_image.id
    assert no_image.id != with_image.id


def test_pick_next_post_falls_back_to_no_image_when_none_have_images(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    raw = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="a")
    only_post = repo.create_processed_post(raw_post_id=raw.id, score=95, status="queued")

    picked = pick_next_post_to_publish(repo, max_posts_per_day=12, freshness_hours=6)

    assert picked.id == only_post.id


@pytest.mark.asyncio
async def test_publishing_scheduler_calls_on_slot_and_swallows_errors():
    schedule = ScheduleConfig(
        mode="interval", fixed_slots=[], interval_minutes=40, max_posts_per_day=12
    )
    on_slot = AsyncMock(side_effect=RuntimeError("падение задачи"))
    scheduler = PublishingScheduler(schedule, on_slot)

    await scheduler._run_slot()  # не должно поднять исключение наружу

    on_slot.assert_awaited_once()
