"""Публикация поста из очереди в VK — аналог queue_service.py для Telegram."""
from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

from app.core.publishing.footer import FooterLinks, build_vk_footer
from app.core.publishing.rate_guard import check_publish_allowed
from app.core.publishing.text_formatting import split_hashtags, strip_markdown
from app.core.publishing.vk_publisher import VKPublisher, VKPublishResult
from app.db.repository import Repository

logger = logging.getLogger("publishing")

DEFAULT_MAX_POSTS_PER_DAY = 6
DEFAULT_MIN_INTERVAL_MINUTES = 180


class PostNotFoundError(Exception):
    pass


def publish_queued_post_vk(
    repo: Repository,
    publisher: VKPublisher,
    *,
    post_id: int,
    group_id: int,
    footer_links: FooterLinks | None = None,
    max_posts_per_day: int = DEFAULT_MAX_POSTS_PER_DAY,
    min_interval_minutes: int = DEFAULT_MIN_INTERVAL_MINUTES,
    include_hashtags: bool = False,
) -> VKPublishResult:
    processed = repo.get_processed_post(post_id)
    if processed is None:
        raise PostNotFoundError(f"processed_post {post_id} не найден")

    blocked = check_publish_allowed(
        repo,
        post_id,
        max_posts_per_day=max_posts_per_day,
        min_interval_minutes=min_interval_minutes,
    )
    if blocked is not None:
        logger.warning("Публикация поста %d в VK отклонена антиспам-стопором: %s", post_id, blocked)
        return VKPublishResult(success=False, post_id=None, error=f"throttled: {blocked}")

    text = _build_vk_publish_text(
        processed.headline, processed.rewritten_text, footer_links, include_hashtags
    )
    image_paths = (
        [Path(p) for p in json.loads(processed.image_paths)] if processed.image_paths else []
    )
    publish_kwargs = dict(group_id=group_id, text=text, image_paths=image_paths)
    if processed.video_path:
        publish_kwargs["video_path"] = Path(processed.video_path)
    result = publisher.publish(**publish_kwargs)

    if result.success:
        repo.update_processed_post_status(
            post_id, "published", published_at=datetime.datetime.utcnow()
        )
    elif not _already_published(repo, post_id):
        # Не понижаем статус, если пост уже опубликован в другой сети (напр. TG прошёл,
        # а VK упал) — см. тот же фикс в queue_service.py.
        repo.update_processed_post_status(post_id, "failed")

    return result


def _already_published(repo: Repository, post_id: int) -> bool:
    current = repo.get_processed_post(post_id)
    return current is not None and current.status == "published"


def _build_vk_publish_text(
    headline: str | None,
    rewritten_text: str | None,
    footer_links: FooterLinks | None,
    include_hashtags: bool = False,
) -> str:
    body, hashtags = split_hashtags(rewritten_text or "")

    parts = []
    if headline:
        parts.append(headline)
    parts.append(strip_markdown(body))

    if footer_links is not None:
        footer = build_vk_footer(footer_links)
        if footer:
            parts.append(footer)

    if hashtags and include_hashtags:
        parts.append(hashtags)

    return "\n\n".join(parts)
