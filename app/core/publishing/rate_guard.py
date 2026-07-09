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


_NETWORKS = ("tg", "vk")


def check_publish_allowed(
    repo: Repository,
    post_id: int,
    *,
    network: str,
    max_posts_per_day: int,
    min_interval_minutes: int,
    channel_id: int | None = None,
    now: datetime.datetime | None = None,
) -> str | None:
    """Возвращает None, если публиковать можно, либо строку-причину, если нельзя.

    КРИТИЧНО (найдено 2026-07-05 — один пост ушёл в TG 28 раз подряд за 5 часов):
    старая версия разрешала ЛЮБУЮ повторную публикацию поста, у которого
    status=='published', без проверки, В КАКУЮ ИМЕННО сеть — то есть однажды
    успешно опубликованный пост навсегда обходил дневной лимит и интервал для
    ЛЮБОЙ сети, включая ту же самую. Теперь публикация в СЕТЬ, где пост уже
    выходил, всегда заблокирована; разрешён только законный кросс-пост во
    ВТОРУЮ сеть, где его ещё не было.
    """
    if network not in _NETWORKS:
        raise ValueError(f"Неизвестная сеть: {network!r}, ожидается одна из {_NETWORKS}")

    already_this_network = repo.get_published_network_at(post_id, network) is not None
    if already_this_network:
        return f"пост уже опубликован в {network} ранее — повторная публикация в ту же сеть заблокирована"

    other_network = next(n for n in _NETWORKS if n != network)
    already_other_network = repo.get_published_network_at(post_id, other_network) is not None
    if already_other_network:
        return None  # законный кросс-пост во вторую сеть — не считать за новый релиз

    now = now or datetime.datetime.utcnow()
    since = _start_of_today_utc(now)

    published_today = repo.count_published_since(since, channel_id=channel_id)
    if published_today >= max_posts_per_day:
        return f"дневной лимит публикаций достигнут ({published_today}/{max_posts_per_day})"

    last_published_at = repo.get_last_published_at(channel_id=channel_id)
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
