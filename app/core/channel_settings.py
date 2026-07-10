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

    image_query_mode: str = "generic"
    """"generic" → обычный сток-запрос по смыслу текста (Pexels/Unsplash/Pixabay).
    "movie_title" → LLM извлекает НАЗВАНИЕ ФИЛЬМА из текста, ищем реальные кадры/постеры
    (кино-канал) через image_providers_order (обычно ["google"]), а не сток."""

    image_providers_order: list[str] | None = None
    """Переопределяет глобальный images.providers_order для этого канала. None →
    глобальный порядок. Обязателен при image_query_mode="movie_title" (сток не найдёт
    кадры конкретного фильма)."""

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
            image_query_mode=data.get("image_query_mode", "generic"),
            image_providers_order=data.get("image_providers_order"),
        )

    def to_json(self) -> str:
        payload: dict = {"filters_enabled": self.filters_enabled}
        if self.max_posts_per_day is not None:
            payload["max_posts_per_day"] = self.max_posts_per_day
        if self.min_interval_minutes is not None:
            payload["min_interval_minutes"] = self.min_interval_minutes
        if self.tg_footer_url is not None:
            payload["tg_footer_url"] = self.tg_footer_url
        if self.image_query_mode != "generic":
            payload["image_query_mode"] = self.image_query_mode
        if self.image_providers_order is not None:
            payload["image_providers_order"] = self.image_providers_order
        return json.dumps(payload, ensure_ascii=False)
