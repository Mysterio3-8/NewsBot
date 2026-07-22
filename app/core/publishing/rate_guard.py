"""Жёсткий стопор публикации (защита от спама/бана аккаунта).

Соблюдается на самом слое публикации: любой вызов `publish_queued_post*` — хоть
из планировщика, хоть ручной, хоть ошибочный batch — проходит через эту проверку.
Ограничения нельзя обойти, минуя планировщик (именно так VK забанили 2026-07-02:
ручной скрипт опубликовал 12 постов подряд в обход `pick_next_post_to_publish`).
"""
from __future__ import annotations

import datetime
import random

from app.db.repository import Repository


def _start_of_today_utc(now: datetime.datetime) -> datetime.datetime:
    return datetime.datetime(now.year, now.month, now.day, tzinfo=now.tzinfo)


_NETWORKS = ("tg", "vk")

MSK_OFFSET_HOURS = 3  # часы расписания заданы в МСК, а now — в UTC


def is_quiet_now(
    now_utc: datetime.datetime,
    quiet_start_hour: int | None,
    quiet_end_hour: int | None,
) -> bool:
    """Ночная пауза (антибан VK: 6-8 ч без активности ночью выглядит как человек).
    Часы задаются в МСК, окно может пересекать полночь (напр. 0..7 или 23..6).
    Оба None или равны → пауза выключена."""
    if quiet_start_hour is None or quiet_end_hour is None or quiet_start_hour == quiet_end_hour:
        return False
    msk_hour = (now_utc.hour + MSK_OFFSET_HOURS) % 24
    if quiet_start_hour < quiet_end_hour:
        return quiet_start_hour <= msk_hour < quiet_end_hour
    return msk_hour >= quiet_start_hour or msk_hour < quiet_end_hour  # окно через полночь


def check_publish_allowed(
    repo: Repository,
    post_id: int,
    *,
    network: str,
    max_posts_per_day: int,
    min_interval_minutes: int,
    max_interval_minutes: int | None = None,
    quiet_start_hour: int | None = None,
    quiet_end_hour: int | None = None,
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

    now = now or datetime.datetime.utcnow()
    if is_quiet_now(now, quiet_start_hour, quiet_end_hour):
        return f"ночная пауза ({quiet_start_hour}:00–{quiet_end_hour}:00 МСК) — публикация отложена"

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
        required = required_interval_minutes(
            min_interval_minutes, max_interval_minutes, last_published_at
        )
        if elapsed_minutes < required:
            return (
                f"слишком рано после прошлой публикации "
                f"({elapsed_minutes:.0f} мин < минимум {required:.0f} мин)"
            )

    return None


def required_interval_minutes(
    min_interval_minutes: int,
    max_interval_minutes: int | None,
    last_published_at: datetime.datetime,
) -> float:
    """Случайная пауза до следующей публикации в [min, max] (ТЗ 2026-07-21: 1.5-4 часа
    на рандом). Значение детерминировано временем ПРОШЛОЙ публикации — иначе оно бы
    выпадало заново на каждой проверке очереди, и пост уходил бы при первом же удачном
    броске, что превращает диапазон в его нижнюю границу."""
    if max_interval_minutes is None or max_interval_minutes <= min_interval_minutes:
        return float(min_interval_minutes)
    seed = int(last_published_at.timestamp())
    return random.Random(seed).uniform(min_interval_minutes, max_interval_minutes)
