"""Чтение постов Telegram-каналов через Telethon (MTProto, раздел 7 SPEC.md).

Bot API не подходит: он не может получить историю/новые посты чужого канала,
если бот не администратор этого канала. Источники — произвольные новостные
каналы, поэтому используется MTProto-клиент от личного аккаунта пользователя.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import socks
from telethon import TelegramClient

from app.core.monitoring.models import FetchedPost
from app.paths import OUTPUT_DIR

logger = logging.getLogger("monitoring")

# Telegram не даёт прямых HTTP-URL на медиа (в отличие от VK) — только скачивание
# через саму Telethon-сессию. Скачиваем сюда, дальше SourceImageProvider читает
# как обычный local_path (та же цепочка watermark/уникализации, что и для VK-фото).
TG_MEDIA_DIR = OUTPUT_DIR / "tg_raw_media"


def detect_telethon_proxy() -> tuple[int, str, int] | None:
    """Telethon использует сырой MTProto, не aiohttp/requests — тот же локальный
    HTTP_PROXY/HTTPS_PROXY нужно передать явно через PySocks-совместимый кортеж.
    """
    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url)
    return (socks.HTTP, parsed.hostname, parsed.port)


def message_to_post(
    message: Any, *, media_paths: list[str] | None = None, video_path: str | None = None
) -> FetchedPost:
    """Преобразование Telethon Message в FetchedPost. Чистая функция — тестируется без сети.
    media_paths/video_path — уже скачанные локальные пути (см. TelegramFetcher._download_photo/
    _download_video), передаются отдельно, т.к. скачивание асинхронное и требует клиента."""
    return FetchedPost(
        external_id=str(message.id),
        text=message.message or "",
        post_type=_classify_message_type(message),
        views=getattr(message, "views", None) or 0,
        published_at=message.date,
        has_media=getattr(message, "media", None) is not None,
        media_urls=media_paths or [],
        video_path=video_path,
    )


def _classify_message_type(message: Any) -> str:
    if getattr(message, "action", None) is not None:
        return "service"
    if getattr(message, "poll", None) is not None:
        return "poll"
    if getattr(message, "pinned", False):
        return "pinned"
    return "text"


_NO_GROUP = object()


def group_album_messages(messages: list[Any]) -> list[list[Any]]:
    """Склейка сообщений одного альбома (Telegram шлёт альбом как несколько
    отдельных сообщений с общим grouped_id) в одну группу. messages — по
    возрастанию id, поэтому члены альбома идут подряд. Сообщения без grouped_id —
    каждое отдельной группой (в т.ч. два подряд без группы не сливаются).
    Чистая функция — тестируется без сети."""
    groups: list[list[Any]] = []
    current: list[Any] = []
    current_gid: Any = _NO_GROUP
    for message in messages:
        gid = getattr(message, "grouped_id", None)
        if gid is not None and current and gid == current_gid:
            current.append(message)
            continue
        if current:
            groups.append(current)
        current = [message]
        current_gid = gid
    if current:
        groups.append(current)
    return groups


def _album_representative(group: list[Any]) -> Any:
    """Текст альбома лежит на одном из его сообщений (обычно первом или последнем) —
    берём первое с непустым текстом, иначе первое сообщение группы."""
    for message in group:
        if (getattr(message, "message", None) or "").strip():
            return message
    return group[0]


def album_to_post(
    group: list[Any], *, media_paths: list[str] | None = None, video_path: str | None = None
) -> FetchedPost:
    """FetchedPost из группы-альбома: один пост на весь альбом, все медиа вместе.
    external_id — максимальный id в группе (курсор перешагивает весь альбом за раз)."""
    rep = _album_representative(group)
    max_id = max(int(message.id) for message in group)
    return FetchedPost(
        external_id=str(max_id),
        text=rep.message or "",
        post_type=_classify_message_type(rep),
        views=getattr(rep, "views", None) or 0,
        published_at=rep.date,
        has_media=any(getattr(message, "media", None) is not None for message in group),
        media_urls=media_paths or [],
        video_path=video_path,
    )


class TelegramFetcher:
    def __init__(self, api_id: int, api_hash: str, session_name: str) -> None:
        self._client = TelegramClient(
            session_name, api_id, api_hash, proxy=detect_telethon_proxy()
        )

    async def fetch_recent_posts(
        self,
        channel_url: str,
        *,
        max_age_hours: float,
        limit: int = 50,
        known_external_ids: set[str] | None = None,
    ) -> list[FetchedPost]:
        """known_external_ids — последние обработанные ID этого источника (см.
        Repository.get_recent_external_ids). Без этого фильтра одни и те же
        сообщения скачивались бы заново на КАЖДОМ цикле проверки, пока они попадают
        в окно max_age_hours — на проде это раздуло диск до 100% (один файл скачан
        245 раз) прежде чем нашли причину."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        known_ids = known_external_ids or set()
        posts: list[FetchedPost] = []

        async with self._client:
            async for message in self._client.iter_messages(channel_url, limit=limit):
                if message.date < cutoff:
                    break  # iter_messages отдаёт от новых к старым — дальше только старее
                if str(message.id) in known_ids:
                    continue
                media_paths = await self._download_photo(message)
                video_path = await self._download_video(message)
                posts.append(
                    message_to_post(message, media_paths=media_paths, video_path=video_path)
                )

        return posts

    async def fetch_new_posts(
        self,
        channel_url: str,
        *,
        after_id: int,
        limit: int = 50,
        known_external_ids: set[str] | None = None,
    ) -> list[FetchedPost]:
        """Мониторинг по курсору last_processed_message_id, а не по времени публикации
        (запрос пользователя 2026-07-07). Берём ВСЕ сообщения с id > after_id, по
        возрастанию id, склеивая альбомы в один пост — так пачка постов, вышедшая
        между проверками, не теряется (раньше по времени бот брал только самый свежий).
        known_external_ids — вторичная защита от повторной обработки (курсор уже их
        исключает), как в fetch_recent_posts."""
        known_ids = known_external_ids or set()
        posts: list[FetchedPost] = []

        async with self._client:
            messages = [
                message
                async for message in self._client.iter_messages(
                    channel_url, min_id=after_id, limit=limit
                )
            ]
            # min_id в Telethon — нижняя граница; фильтр по id > after_id явно, чтобы
            # не зависеть от inclusive/exclusive поведения версии библиотеки.
            messages = [m for m in messages if int(m.id) > after_id]
            messages.sort(key=lambda m: int(m.id))  # по возрастанию — порядок канала

            for group in group_album_messages(messages):
                external_id = str(max(int(m.id) for m in group))
                if external_id in known_ids:
                    continue
                media_paths: list[str] = []
                video_path: str | None = None
                for message in group:
                    media_paths.extend(await self._download_photo(message))
                    video = await self._download_video(message)
                    if video is not None and video_path is None:
                        video_path = video
                posts.append(
                    album_to_post(group, media_paths=media_paths, video_path=video_path)
                )

        return posts

    async def get_latest_message_id(self, channel_url: str) -> int | None:
        """id самого свежего сообщения канала — для инициализации курсора на первом
        запуске (чтобы не бэкфилить всю историю канала, а начать «с этого момента»)."""
        async with self._client:
            async for message in self._client.iter_messages(channel_url, limit=1):
                return int(message.id)
        return None

    async def _download_photo(self, message: Any) -> list[str]:
        if message.photo is None:
            return []
        TG_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            path = await self._client.download_media(message, file=str(TG_MEDIA_DIR) + os.sep)
        except Exception as error:
            logger.warning("Не удалось скачать фото из TG-сообщения %d: %s", message.id, error)
            return []
        if path:
            logger.debug("Скачано фото из TG-сообщения %d → %s", message.id, path)
        return [path] if path else []

    async def _download_video(self, message: Any) -> str | None:
        """Telethon download_media работает одинаково для фото и видео — отличается
        только проверка типа медиа. Дальнейшая обработка (watermark, публикация)
        для видео — отдельный пайплайн, см. app/core/video/."""
        if message.video is None:
            return None
        TG_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            path = await self._client.download_media(message, file=str(TG_MEDIA_DIR) + os.sep)
        except Exception as error:
            logger.warning("Не удалось скачать видео из TG-сообщения %d: %s", message.id, error)
            return None
        if path:
            logger.debug("Скачано видео из TG-сообщения %d → %s", message.id, path)
        return path
