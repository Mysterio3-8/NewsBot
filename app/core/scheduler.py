"""Расписание автопубликации (раздел 13.2 SPEC.md)."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

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


def pick_next_post_to_publish(
    repo: Repository,
    *,
    max_posts_per_day: int,
    freshness_hours: int,
) -> ProcessedPost | None:
    """Только СВЕЖИЕ новости, без залежавшейся очереди (запрос пользователя
    2026-07-05: "публикуй только свежие новости без очереди"). Выбираем самый
    свежий пост очереди (по created_at); старьё за пределами `freshness_hours`
    протухает и никогда не публикуется. Возвращает None, если свежих нет или
    достигнут дневной лимит.

    Ранее выбирали пост с максимальным score без учёта возраста — из-за этого
    публиковались старые новости (в т.ч. с уже впечатанными в старые рендеры
    чужими подписями, когда функция channel_name ещё существовала)."""
    published_today = repo.count_published_since(start_of_today_utc())
    if published_today >= max_posts_per_day:
        return None

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=freshness_hours)
    repo.expire_stale_queued_posts(cutoff)

    fresh_posts = repo.list_fresh_queued_posts(cutoff)  # свежайшие первыми
    if not fresh_posts:
        return None
    return _prefer_post_with_image(fresh_posts)


def _has_image(post: ProcessedPost) -> bool:
    return bool(post.image_paths) and post.image_paths not in ("[]", "null")


def _prefer_post_with_image(candidates: list[ProcessedPost]) -> ProcessedPost:
    """Пользователь требует медиа на каждом опубликованном посте: среди постов
    одного тира (по важности) выбираем лучший по score СРЕДИ ИМЕЮЩИХ картинку,
    а не просто самый высокий score. Падаем на лучший без картинки только если
    в тире вообще нет постов с изображением."""
    for post in candidates:
        if _has_image(post):
            return post
    return candidates[0]


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
