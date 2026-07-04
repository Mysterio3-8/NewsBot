"""Форматирование UTC-времени из БД в московское для отображения (web + bot).
В БД published_at хранится в UTC; Москва = UTC+3. Конвертация только для показа."""
from __future__ import annotations

import datetime
import zoneinfo

MOSCOW_TZ = zoneinfo.ZoneInfo("Europe/Moscow")


def format_moscow_time(dt: datetime.datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(MOSCOW_TZ).strftime("%d.%m.%Y %H:%M")
