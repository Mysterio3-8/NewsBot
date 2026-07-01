"""Ручная публикация поста из очереди (критерий готовности MVP, раздел 21 SPEC.md)."""
from __future__ import annotations

import datetime
import html

from app.core.publishing.footer import FooterLinks, build_html_footer
from app.core.publishing.telegram_publisher import PublishResult, TelegramPublisher
from app.db.repository import Repository


class PostNotFoundError(Exception):
    pass


async def publish_queued_post(
    repo: Repository,
    publisher: TelegramPublisher,
    *,
    post_id: int,
    chat_id: str,
    footer_links: FooterLinks | None = None,
) -> PublishResult:
    processed = repo.get_processed_post(post_id)
    if processed is None:
        raise PostNotFoundError(f"processed_post {post_id} не найден")

    text = _build_publish_text(processed.headline, processed.rewritten_text, footer_links)
    result = await publisher.publish(chat_id=chat_id, text=text, parse_mode="HTML")

    if result.success:
        repo.update_processed_post_status(
            post_id, "published", published_at=datetime.datetime.utcnow()
        )
    else:
        repo.update_processed_post_status(post_id, "failed")

    return result


def _build_publish_text(
    headline: str | None,
    rewritten_text: str | None,
    footer_links: FooterLinks | None,
) -> str:
    parts = []
    if headline:
        parts.append(html.escape(headline))
    parts.append(html.escape(rewritten_text or ""))

    if footer_links is not None:
        footer = build_html_footer(footer_links)
        if footer:
            parts.append(footer)

    return "\n\n".join(parts)
