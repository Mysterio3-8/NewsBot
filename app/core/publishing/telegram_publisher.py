"""Публикация в Telegram через aiogram (раздел 13.3-13.4 SPEC.md)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile, InputMediaPhoto

logger = logging.getLogger("publishing")

CAPTION_LIMIT = 1024
RETRY_DELAYS_SECONDS = [0, 5, 30, 120]


@dataclass(frozen=True)
class PublishResult:
    success: bool
    message_id: int | None
    error: str | None


def split_caption(text: str, limit: int = CAPTION_LIMIT) -> tuple[str, str | None]:
    if len(text) <= limit:
        return text, None
    return text[:limit], text[limit:]


class TelegramPublisher:
    def __init__(self, bot_token: str) -> None:
        self._bot = Bot(token=bot_token)

    async def publish(
        self, *, chat_id: str, text: str, image_paths: list[Path] | None = None
    ) -> PublishResult:
        image_paths = image_paths or []
        last_error: Exception | None = None

        for attempt, delay in enumerate(RETRY_DELAYS_SECONDS, start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                message = await self._send(chat_id=chat_id, text=text, image_paths=image_paths)
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

    async def _send(self, *, chat_id: str, text: str, image_paths: list[Path]):
        caption, extra_text = split_caption(text)

        if not image_paths:
            message = await self._bot.send_message(chat_id, text)
        elif len(image_paths) == 1:
            message = await self._bot.send_photo(
                chat_id, FSInputFile(image_paths[0]), caption=caption
            )
        else:
            media = [InputMediaPhoto(media=FSInputFile(p)) for p in image_paths]
            media[0].caption = caption
            messages = await self._bot.send_media_group(chat_id, media)
            message = messages[0]

        if extra_text:
            await self._bot.send_message(chat_id, extra_text)

        return message
