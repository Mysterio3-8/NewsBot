"""Чтение постов Telegram-каналов через Telethon (MTProto, раздел 7 SPEC.md).

Bot API не подходит: он не может получить историю/новые посты чужого канала,
если бот не администратор этого канала. Источники — произвольные новостные
каналы, поэтому используется MTProto-клиент от личного аккаунта пользователя.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import socks
from telethon import TelegramClient

from app.core.monitoring.models import FetchedPost


def detect_telethon_proxy() -> tuple[int, str, int] | None:
    """Telethon использует сырой MTProto, не aiohttp/requests — тот же локальный
    HTTP_PROXY/HTTPS_PROXY нужно передать явно через PySocks-совместимый кортеж.
    """
    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url)
    return (socks.HTTP, parsed.hostname, parsed.port)


def message_to_post(message: Any) -> FetchedPost:
    """Преобразование Telethon Message в FetchedPost. Чистая функция — тестируется без сети."""
    return FetchedPost(
        external_id=str(message.id),
        text=message.message or "",
        post_type=_classify_message_type(message),
        views=getattr(message, "views", None) or 0,
        published_at=message.date,
        has_media=getattr(message, "media", None) is not None,
    )


def _classify_message_type(message: Any) -> str:
    if getattr(message, "action", None) is not None:
        return "service"
    if getattr(message, "poll", None) is not None:
        return "poll"
    if getattr(message, "pinned", False):
        return "pinned"
    return "text"


class TelegramFetcher:
    def __init__(self, api_id: int, api_hash: str, session_name: str) -> None:
        self._client = TelegramClient(
            session_name, api_id, api_hash, proxy=detect_telethon_proxy()
        )

    async def fetch_recent_posts(
        self, channel_url: str, *, max_age_hours: float, limit: int = 50
    ) -> list[FetchedPost]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        posts: list[FetchedPost] = []

        async with self._client:
            async for message in self._client.iter_messages(channel_url, limit=limit):
                if message.date < cutoff:
                    break  # iter_messages отдаёт от новых к старым — дальше только старее
                posts.append(message_to_post(message))

        return posts
