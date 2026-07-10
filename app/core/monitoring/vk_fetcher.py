"""Чтение постов VK-сообществ через vk_api (раздел 7 SPEC.md)."""
from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
import vk_api

from app.core.monitoring.models import FetchedPost
from app.core.publishing.token_bucket import TokenBucket, token_key
from app.paths import OUTPUT_DIR

logger = logging.getLogger("monitoring")

# VK отдаёт прямые HTTP-URL на CDN (в отличие от Telegram) — но весь остальной
# пайплайн (detect_foreign_watermark, Watermarker, VKPublisher._upload_photo)
# ожидает ЛОКАЛЬНЫЙ путь файла и делает Image.open()/open(path, "rb") — на
# сырой URL это тихо падает (ловится try/except выше по стеку) и пост уходит
# без фото. Скачиваем сюда, как TelegramFetcher._download_photo (см. там же).
VK_MEDIA_DIR = OUTPUT_DIR / "vk_raw_media"


def vk_post_to_fetched_post(item: dict[str, Any]) -> FetchedPost:
    """Преобразование ответа wall.get в FetchedPost. Чистая функция — тестируется без сети."""
    return FetchedPost(
        external_id=str(item["id"]),
        text=item.get("text", ""),
        post_type=_classify_vk_post_type(item),
        views=item.get("views", {}).get("count", 0),
        published_at=datetime.fromtimestamp(item["date"], tz=timezone.utc),
        has_media=bool(item.get("attachments")),
        media_urls=_extract_photo_urls(item),
    )


def _extract_photo_urls(item: dict[str, Any]) -> list[str]:
    urls = []
    for attachment in item.get("attachments", []):
        if attachment.get("type") != "photo":
            continue
        sizes = attachment.get("photo", {}).get("sizes", [])
        if not sizes:
            continue
        largest = max(sizes, key=lambda s: s.get("width", 0) * s.get("height", 0))
        urls.append(largest["url"])
    return urls


def _classify_vk_post_type(item: dict[str, Any]) -> str:
    if item.get("marked_as_ads"):
        return "ad"
    if item.get("is_pinned"):
        return "pinned"
    attachments = item.get("attachments", [])
    if any(a.get("type") == "poll" for a in attachments):
        return "poll"
    return "text"


class VKFetcher:
    # Дефолты класса — тесты создают экземпляр через __new__ (минуя __init__) и не
    # трогают лимитер; без этих дефолтов такой экземпляр падал бы AttributeError.
    _bucket: TokenBucket | None = None
    _bucket_key: str | None = None

    def __init__(self, user_token: str, *, token_bucket: TokenBucket | None = None) -> None:
        """token_bucket — общий процесс-wide лимитер (ТЗ: не более 2 запр/сек на
        токен). Личный VK-токен (VK_USER_TOKEN) используется ТОЛЬКО для чтения
        источников — минимизируем и темп его вызовов через этот лимитер."""
        session = vk_api.VkApi(token=user_token)
        self._api = session.get_api()
        self._bucket = token_bucket
        self._bucket_key = token_key(user_token)

    def fetch_recent_posts(
        self,
        group_id: int,
        *,
        max_age_hours: float,
        count: int = 50,
        known_external_ids: set[str] | None = None,
    ) -> list[FetchedPost]:
        """known_external_ids — последние обработанные ID этого источника (см.
        Repository.get_recent_external_ids). Без этого фильтра фото уже известных
        постов скачивались бы заново на КАЖДОМ цикле проверки, пока пост не выпадет
        из окна max_age_hours — тот же баг, что забивал диск на 100% для TelegramFetcher
        (см. известные грабли в CLAUDE.md), пока там не добавили тот же фильтр."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        known_ids = known_external_ids or set()
        posts: list[FetchedPost] = []

        if self._bucket is not None:
            self._bucket.wait(self._bucket_key)
        response = self._api.wall.get(owner_id=-abs(group_id), count=count)
        for item in response["items"]:
            post = vk_post_to_fetched_post(item)
            if post.published_at < cutoff:
                continue
            if post.external_id in known_ids:
                continue
            local_paths = self._download_photos(post.external_id, post.media_urls)
            posts.append(dataclasses.replace(post, media_urls=local_paths))

        return posts

    def _download_photos(self, external_id: str, urls: list[str]) -> list[str]:
        if not urls:
            return []
        VK_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        local_paths: list[str] = []
        for index, url in enumerate(urls):
            dest = VK_MEDIA_DIR / f"{external_id}_{index}.jpg"
            try:
                response = requests.get(url, timeout=30)
                response.raise_for_status()
                dest.write_bytes(response.content)
            except Exception as error:
                logger.warning("VK: не удалось скачать фото поста %s: %s", external_id, error)
                continue
            local_paths.append(str(dest))
        return local_paths
