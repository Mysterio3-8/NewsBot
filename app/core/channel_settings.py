"""Настройки канала из Channel.settings_json (мультиканальность).

Переопределяют глобальные дефолты config.yaml для конкретного канала. Все поля
опциональны — дефолт/None означает «наследовать глобальную настройку». Хранятся в БД
как JSON (Channel.settings_json), чтобы добавлять настройки без миграций схемы.
"""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelSettings:
    filters_enabled: bool = True
    """False → «лить всё подряд» (кино/мемы): пропускаем LLM-гейт новостей и порог
    min_score, оставляем только дедуп (не публиковать один пост дважды). True → полная
    новостная фильтрация, как у Канала 1."""

    max_posts_per_day: int | None = None
    """None → глобальный лимит из config. Иначе — свой дневной лимит канала."""

    min_interval_minutes: int | None = None
    """Минимум минут между публикациями канала (защита от пачки). None → глобальный."""

    tg_footer_url: str | None = None
    """Ссылка, добавляемая в конец поста этого канала (напр. ссылка на TG-канал). None → нет."""

    @classmethod
    def from_json(cls, raw: str | None) -> "ChannelSettings":
        if not raw:
            return cls()
        data = json.loads(raw)
        return cls(
            filters_enabled=data.get("filters_enabled", True),
            max_posts_per_day=data.get("max_posts_per_day"),
            min_interval_minutes=data.get("min_interval_minutes"),
            tg_footer_url=data.get("tg_footer_url"),
        )

    def to_json(self) -> str:
        payload: dict = {"filters_enabled": self.filters_enabled}
        if self.max_posts_per_day is not None:
            payload["max_posts_per_day"] = self.max_posts_per_day
        if self.min_interval_minutes is not None:
            payload["min_interval_minutes"] = self.min_interval_minutes
        if self.tg_footer_url is not None:
            payload["tg_footer_url"] = self.tg_footer_url
        return json.dumps(payload, ensure_ascii=False)
