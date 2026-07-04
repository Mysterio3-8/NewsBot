"""Чтение постов VK-сообществ через vk_api (раздел 7 SPEC.md)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import vk_api

from app.core.monitoring.models import FetchedPost


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
    def __init__(self, user_token: str) -> None:
        session = vk_api.VkApi(token=user_token)
        self._api = session.get_api()

    def fetch_recent_posts(
        self, group_id: int, *, max_age_hours: float, count: int = 50
    ) -> list[FetchedPost]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        response = self._api.wall.get(owner_id=-abs(group_id), count=count)

        posts: list[FetchedPost] = []
        for item in response["items"]:
            post = vk_post_to_fetched_post(item)
            if post.published_at < cutoff:
                continue
            posts.append(post)

        return posts
