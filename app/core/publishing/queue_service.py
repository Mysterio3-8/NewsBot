"""Ручная публикация поста из очереди (критерий готовности MVP, раздел 21 SPEC.md)."""
from __future__ import annotations

import datetime

from app.core.publishing.telegram_publisher import PublishResult, TelegramPublisher
from app.db.repository import Repository


class PostNotFoundError(Exception):
    pass


async def publish_queued_post(
    repo: Repository, publisher: TelegramPublisher, *, post_id: int, chat_id: str
) -> PublishResult:
    processed = repo.get_processed_post(post_id)
    if processed is None:
        raise PostNotFoundError(f"processed_post {post_id} не найден")

    text = _build_publish_text(processed.headline, processed.rewritten_text)
    result = await publisher.publish(chat_id=chat_id, text=text)

    if result.success:
        repo.update_processed_post_status(
            post_id, "published", published_at=datetime.datetime.utcnow()
        )
    else:
        repo.update_processed_post_status(post_id, "failed")

    return result


def _build_publish_text(headline: str | None, rewritten_text: str | None) -> str:
    body = rewritten_text or ""
    if headline:
        return f"{headline}\n\n{body}"
    return body
