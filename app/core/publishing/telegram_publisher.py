"""Публикация в Telegram через aiogram (раздел 13.3-13.4 SPEC.md)."""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import FSInputFile, InputMediaPhoto

logger = logging.getLogger("publishing")

CAPTION_LIMIT = 1024
RETRY_DELAYS_SECONDS = [0, 5, 30, 120]
# Обычный Bot API отклоняет файлы больше 50 МБ — для более крупных видео нужен
# self-hosted Telegram Bot API сервер (отдельная инфраструктурная задача, не
# делать без явного запроса пользователя, см. NEXT_SESSION.md).
BOT_API_FILE_LIMIT_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class PublishResult:
    success: bool
    message_id: int | None
    error: str | None


def split_caption(text: str, limit: int = CAPTION_LIMIT) -> tuple[str, str | None]:
    if len(text) <= limit:
        return text, None
    return text[:limit], text[limit:]


def _media_exists(path: Path, kind: str) -> bool:
    if Path(path).exists():
        return True
    logger.warning("TG: %s не найдено на диске (%s), публикую без него", kind, path)
    return False


def detect_proxy_url() -> str | None:
    """aiohttp (в отличие от requests/curl) не читает HTTPS_PROXY/HTTP_PROXY сам по себе —
    без явной настройки запросы к api.telegram.org зависают там, где сеть требует прокси.
    """
    return os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")


class TelegramPublisher:
    def __init__(self, bot_token: str) -> None:
        proxy_url = detect_proxy_url()
        session = AiohttpSession(proxy=proxy_url) if proxy_url else None
        self._bot = Bot(token=bot_token, session=session)

    async def publish(
        self,
        *,
        chat_id: str,
        text: str,
        image_paths: list[Path] | None = None,
        video_path: Path | None = None,
        parse_mode: str | None = None,
    ) -> PublishResult:
        # Медиа — best-effort (тот же принцип, что и у VKPublisher): путь может не
        # существовать на этой машине (напр. посты, перенесённые с другого хоста —
        # реальный случай при переезде БД на VPS, найдено 2026-07-04). Отсутствующий
        # файл — детерминированная ошибка, 4 повтора с растущей паузой её не исправят,
        # только тратят время — поэтому чистим ДО входа в retry-цикл, а не ловим внутри.
        image_paths = [p for p in (image_paths or []) if _media_exists(p, "фото")]
        if video_path is not None and not _media_exists(video_path, "видео"):
            video_path = None

        if video_path is not None:
            size = Path(video_path).stat().st_size
            if size > BOT_API_FILE_LIMIT_BYTES:
                error = (
                    f"Видео {video_path} ({size} байт) превышает лимит Bot API 50 МБ — "
                    "нужен self-hosted Telegram Bot API сервер"
                )
                logger.error(error)
                return PublishResult(success=False, message_id=None, error=error)

        last_error: Exception | None = None

        for attempt, delay in enumerate(RETRY_DELAYS_SECONDS, start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                message = await self._send(
                    chat_id=chat_id,
                    text=text,
                    image_paths=image_paths,
                    video_path=video_path,
                    parse_mode=parse_mode,
                )
                return PublishResult(success=True, message_id=message.message_id, error=None)
            except Exception as error:  # aiogram поднимает разные TelegramAPIError-подклассы
                last_error = error
                logger.warning(
                    "Публикация не удалась (попытка %d/%d): %s",
                    attempt,
                    len(RETRY_DELAYS_SECONDS),
                    error,
                )

        logger.error("Публикация не удалась после всех попыток: %s", last_error)
        return PublishResult(success=False, message_id=None, error=str(last_error))

    async def _send(
        self,
        *,
        chat_id: str,
        text: str,
        image_paths: list[Path],
        video_path: Path | None,
        parse_mode: str | None,
    ):
        if video_path is not None:
            caption, extra_text = split_caption(text)
            message = await self._bot.send_video(
                chat_id, FSInputFile(video_path), caption=caption, parse_mode=parse_mode
            )
            if extra_text:
                await self._bot.send_message(chat_id, extra_text, parse_mode=parse_mode)
            return message

        if not image_paths:
            # send_message лимит — 1024 у caption фото, а не у обычного текста (4096) —
            # split_caption тут не нужен, иначе "хвост" улетает отдельным сообщением
            # и может обрезать HTML-тег посередине (реальный баг, найден 2026-07-02).
            return await self._bot.send_message(chat_id, text, parse_mode=parse_mode)

        caption, extra_text = split_caption(text)
        if len(image_paths) == 1:
            message = await self._bot.send_photo(
                chat_id, FSInputFile(image_paths[0]), caption=caption, parse_mode=parse_mode
            )
        else:
            media = [InputMediaPhoto(media=FSInputFile(p)) for p in image_paths]
            media[0].caption = caption
            media[0].parse_mode = parse_mode
            messages = await self._bot.send_media_group(chat_id, media)
            message = messages[0]

        if extra_text:
            await self._bot.send_message(chat_id, extra_text, parse_mode=parse_mode)

        return message
