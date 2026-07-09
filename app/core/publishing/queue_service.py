"""Публикация поста из очереди в Telegram (критерий готовности MVP, раздел 21 SPEC.md)."""
from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

from app.core.publishing.footer import FooterLinks, build_html_footer
from app.core.publishing.rate_guard import check_publish_allowed
from app.core.publishing.telegram_publisher import PublishResult, TelegramPublisher
from app.core.publishing.text_formatting import markdown_to_telegram_html, split_hashtags
from app.db.repository import Repository

logger = logging.getLogger("publishing")

# Безопасные дефолты антиспам-стопора — если вызвать publish_queued_post и забыть
# передать лимиты, всё равно действует консервативная защита (не даёт заспамить/бан).
DEFAULT_MAX_POSTS_PER_DAY = 6
DEFAULT_MIN_INTERVAL_MINUTES = 180


class PostNotFoundError(Exception):
    pass


async def publish_queued_post(
    repo: Repository,
    publisher: TelegramPublisher,
    *,
    post_id: int,
    chat_id: str,
    footer_links: FooterLinks | None = None,
    max_posts_per_day: int = DEFAULT_MAX_POSTS_PER_DAY,
    min_interval_minutes: int = DEFAULT_MIN_INTERVAL_MINUTES,
    channel_id: int | None = None,
    include_hashtags: bool = False,
) -> PublishResult:
    processed = repo.get_processed_post(post_id)
    if processed is None:
        raise PostNotFoundError(f"processed_post {post_id} не найден")

    blocked = check_publish_allowed(
        repo,
        post_id,
        network="tg",
        max_posts_per_day=max_posts_per_day,
        min_interval_minutes=min_interval_minutes,
        channel_id=channel_id,
    )
    if blocked is not None:
        # Пост НЕ помечаем failed — он остаётся queued и уйдёт, когда стопор снимется.
        logger.warning("Публикация поста %d отклонена антиспам-стопором: %s", post_id, blocked)
        return PublishResult(success=False, message_id=None, error=f"throttled: {blocked}")

    text = _build_publish_text(
        processed.headline, processed.rewritten_text, footer_links, include_hashtags
    )
    image_paths = (
        [Path(p) for p in json.loads(processed.image_paths)] if processed.image_paths else []
    )
    publish_kwargs = dict(chat_id=chat_id, text=text, image_paths=image_paths, parse_mode="HTML")
    if processed.video_path:
        publish_kwargs["video_path"] = Path(processed.video_path)
    result = await publisher.publish(**publish_kwargs)

    if result.success:
        repo.mark_published(post_id, "tg", datetime.datetime.utcnow())
        logger.info("Пост %d опубликован в TG (message_id=%s)", post_id, result.message_id)
    elif not _already_published(repo, post_id):
        # Не понижаем статус, если пост уже опубликован в другой сети (напр. VK прошёл,
        # а TG упал) — иначе успешная публикация в одной сети выглядела бы как провал
        # и сбивала бы rate_guard (get_last_published_at ищет только status=='published').
        repo.update_processed_post_status(post_id, "failed")

    return result


def _already_published(repo: Repository, post_id: int) -> bool:
    current = repo.get_processed_post(post_id)
    return current is not None and current.status == "published"


def _build_publish_text(
    headline: str | None,
    rewritten_text: str | None,
    footer_links: FooterLinks | None,
    include_hashtags: bool = False,
) -> str:
    """headline не публикуется отдельной строкой (по запросу пользователя 2026-07-04) —
    рерайт уже открывается хук-предложением, дублирующим суть заголовка, и получалось
    троекратное повторение одного факта в начале поста (заголовок + хук-предложение +
    начало тела). headline остаётся полем в БД (используется для статуса/поиска картинок),
    просто не идёт в опубликованный текст."""
    body, hashtags = split_hashtags(rewritten_text or "")

    parts = [markdown_to_telegram_html(body)]

    if footer_links is not None:
        footer = build_html_footer(footer_links)
        if footer:
            parts.append(footer)

    if hashtags and include_hashtags:
        parts.append(hashtags)

    return "\n\n".join(parts)
