"""Публикация большого видео в Telegram-канал через MTProto (Telethon).

Bot API не принимает файлы больше 50 МБ, а фильм в 720p — это сотни мегабайт, поэтому
ежедневный видео-репост уходит в TG не ботом, а пользовательской сессией (лимит 2 ГБ).
Сессия берётся ОТДЕЛЬНОЙ КОПИЕЙ файла Telethon-сессии: тот же аккаунт, но свой файл —
иначе SQLite-сессия, которую уже держит открытой TelegramFetcher, ловит "database is
locked". Копия делается один раз, дальше живёт сама.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from telethon import TelegramClient

from app.core.monitoring.telegram_fetcher import detect_telethon_proxy

logger = logging.getLogger("publishing")

CAPTION_LIMIT = 1024


class TelethonVideoPublisher:
    def __init__(self, *, api_id: int, api_hash: str, session_name: str) -> None:
        self._api_id = api_id
        self._api_hash = api_hash
        self._session_name = session_name

    def publish_video(self, *, destination: str, video_path: Path, caption: str) -> bool:
        """True — видео ушло в канал. Ошибку не поднимаем: репост в VK уже состоялся,
        и падение TG-заливки не должно отменять день."""
        try:
            return asyncio.run(self._publish(destination, video_path, caption))
        except Exception:
            logger.exception("TG-видео: заливка %s в %s не удалась", video_path.name, destination)
            return False

    async def _publish(self, destination: str, video_path: Path, caption: str) -> bool:
        session = _upload_session_path(self._session_name)
        client = TelegramClient(
            str(session), self._api_id, self._api_hash, proxy=detect_telethon_proxy()
        )
        await client.connect()
        try:
            if not await client.is_user_authorized():
                logger.error("TG-видео: сессия %s не авторизована", session)
                return False
            await client.send_file(
                destination,
                str(video_path),
                caption=caption[:CAPTION_LIMIT],
                supports_streaming=True,
            )
            logger.info("TG-видео: %s опубликовано в %s", video_path.name, destination)
            return True
        finally:
            await client.disconnect()


def _upload_session_path(session_name: str) -> Path:
    source = Path(f"{session_name}.session")
    upload = Path(f"{session_name}_upload.session")
    if not upload.exists() and source.exists():
        shutil.copy(source, upload)
        logger.info("TG-видео: создана копия сессии %s", upload)
    return upload.with_suffix("")
