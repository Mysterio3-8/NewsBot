"""Расписание автопубликации (раздел 13.2 SPEC.md)."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config.loader import ScheduleConfig
from app.db.models import ProcessedPost
from app.db.repository import Repository

logger = logging.getLogger("app")


def build_triggers(schedule: ScheduleConfig) -> list[CronTrigger | IntervalTrigger]:
    if schedule.mode == "fixed_slots":
        return [_slot_to_cron_trigger(slot) for slot in schedule.fixed_slots]
    if schedule.mode == "interval":
        return [IntervalTrigger(minutes=schedule.interval_minutes)]
    raise ValueError(f"Неизвестный режим расписания: {schedule.mode}")


def _slot_to_cron_trigger(slot: str) -> CronTrigger:
    hour, minute = slot.split(":")
    return CronTrigger(hour=int(hour), minute=int(minute))


def start_of_today_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, now.day, tzinfo=timezone.utc)


def pick_next_post_to_publish(repo: Repository, *, max_posts_per_day: int) -> ProcessedPost | None:
    """Пустой слот пропускается без ошибки, если очередь пуста или лимит дня достигнут."""
    published_today = repo.count_published_since(start_of_today_utc())
    if published_today >= max_posts_per_day:
        return None

    queued_posts = repo.list_processed_posts(status="queued")  # уже отсортировано по score desc
    return queued_posts[0] if queued_posts else None


class PublishingScheduler:
    """Обёртка над APScheduler: на каждый слот вызывает `on_slot`."""

    def __init__(
        self, schedule_config: ScheduleConfig, on_slot: Callable[[], Awaitable[None]]
    ) -> None:
        self._scheduler = AsyncIOScheduler()
        self._on_slot = on_slot
        for trigger in build_triggers(schedule_config):
            self._scheduler.add_job(self._run_slot, trigger)

    async def _run_slot(self) -> None:
        try:
            await self._on_slot()
        except Exception:
            logger.exception("Ошибка в задаче планировщика публикации")

    def start(self) -> None:
        self._scheduler.start()

    def shutdown(self) -> None:
        self._scheduler.shutdown()
