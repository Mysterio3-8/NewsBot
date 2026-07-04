"""Жёсткий стопор публикации (защита от спама/бана аккаунта).

Соблюдается на самом слое публикации: любой вызов `publish_queued_post*` — хоть
из планировщика, хоть ручной, хоть ошибочный batch — проходит через эту проверку.
Ограничения нельзя обойти, минуя планировщик (именно так VK забанили 2026-07-02:
ручной скрипт опубликовал 12 постов подряд в обход `pick_next_post_to_publish`).
"""
from __future__ import annotations

import datetime

from app.db.repository import Repository


def _start_of_today_utc(now: datetime.datetime) -> datetime.datetime:
    return datetime.datetime(now.year, now.month, now.day, tzinfo=now.tzinfo)


def check_publish_allowed(
    repo: Repository,
    post_id: int,
    *,
    max_posts_per_day: int,
    min_interval_minutes: int,
    now: datetime.datetime | None = None,
) -> str | None:
    """Возвращает None, если публиковать можно, либо строку-причину, если нельзя.

    Пост, уже имеющий статус `published` (мы досылаем тот же пост во вторую сеть —
    TG уже отправлен, теперь VK), всегда разрешён: это не новый релиз, а вторая
    площадка одного и того же поста — на дневной лимит/интервал не влияет.
    """
    processed = repo.get_processed_post(post_id)
    if processed is not None and processed.status == "published":
        return None

    now = now or datetime.datetime.utcnow()
    since = _start_of_today_utc(now)

    published_today = repo.count_published_since(since)
    if published_today >= max_posts_per_day:
        return f"дневной лимит публикаций достигнут ({published_today}/{max_posts_per_day})"

    last_published_at = repo.get_last_published_at()
    if last_published_at is not None:
        if last_published_at.tzinfo is not None and now.tzinfo is None:
            now = now.replace(tzinfo=last_published_at.tzinfo)
        elapsed_minutes = (now - last_published_at).total_seconds() / 60
        if elapsed_minutes < min_interval_minutes:
            return (
                f"слишком рано после прошлой публикации "
                f"({elapsed_minutes:.0f} мин < минимум {min_interval_minutes} мин)"
            )

    return None
