"""Еженедельный репост лучшего поста канала по вовлечённости (просмотры + лайки VK).

Запрос пользователя 2026-07-14: раз в неделю перезаливать ЛУЧШИЙ пост Кино за
последние 7 дней. «Лучший» = просмотры + лайки (VK, wall.getById). Репост идёт в
TG+VK напрямую, В ОБХОД rate_guard — это осознанный повторный постинг, а не баг.
"""
from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

from app.core.publishing.footer import FooterLinks
from app.core.publishing.queue_service import _build_publish_text
from app.core.publishing.telegram_publisher import TelegramPublisher
from app.core.publishing.vk_publisher import VKPublisher
from app.core.publishing.vk_queue_service import _build_vk_publish_text
from app.db.models import Channel, ProcessedPost
from app.db.repository import Repository

logger = logging.getLogger("publishing")

REPOST_WINDOW_DAYS = 7


def pick_best_post(
    repo: Repository,
    vk_publisher: VKPublisher,
    channel: Channel,
    *,
    days: int = REPOST_WINDOW_DAYS,
) -> ProcessedPost | None:
    """Лучший пост канала за последние `days` дней по (просмотры + лайки VK). None —
    нет кандидатов (ничего не публиковалось в VK с сохранённым vk_post_id)."""
    since = datetime.datetime.utcnow() - datetime.timedelta(days=days)
    candidates = repo.list_channel_posts_published_to_vk_since(channel.id, since)
    if not candidates:
        return None
    scores = vk_publisher.fetch_engagement(
        int(channel.vk_destination), [c.vk_post_id for c in candidates]
    )
    return max(candidates, key=lambda p: scores.get(p.vk_post_id, 0))


async def repost_best_post(
    repo: Repository,
    channel: Channel,
    *,
    tg_publisher: TelegramPublisher | None,
    vk_publisher: VKPublisher | None,
    footer_links: FooterLinks | None,
    days: int = REPOST_WINDOW_DAYS,
) -> None:
    """Выбрать лучший пост канала за неделю и перезалить его в TG+VK."""
    if vk_publisher is None or not channel.vk_destination:
        return
    best = pick_best_post(repo, vk_publisher, channel, days=days)
    if best is None:
        logger.info("Еженедельный репост [%s]: нет кандидатов за %d дней", channel.name, days)
        return

    image_paths = [Path(p) for p in json.loads(best.image_paths)] if best.image_paths else []
    video = Path(best.video_path) if best.video_path else None
    logger.info("Еженедельный репост [%s]: лучший пост id=%d", channel.name, best.id)

    if tg_publisher is not None and channel.tg_destination:
        try:
            text = _build_publish_text(best.headline, best.rewritten_text, footer_links)
            kwargs = dict(
                chat_id=channel.tg_destination, text=text, image_paths=image_paths, parse_mode="HTML"
            )
            if video:
                kwargs["video_path"] = video
            await tg_publisher.publish(**kwargs)
        except Exception:
            logger.exception("Еженедельный репост в TG не удался")

    try:
        text_vk = _build_vk_publish_text(best.headline, best.rewritten_text, footer_links, False)
        kwargs = dict(group_id=int(channel.vk_destination), text=text_vk, image_paths=image_paths)
        if video:
            kwargs["video_path"] = video
        vk_publisher.publish(**kwargs)
    except Exception:
        logger.exception("Еженедельный репост в VK не удался")
