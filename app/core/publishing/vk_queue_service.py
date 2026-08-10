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
from app.core.video.clip_cutter import probe_duration_seconds
from app.core.video.watermark import probe_dimensions
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
    max_interval_minutes: int | None = None,
    quiet_start_hour: int | None = None,
    quiet_end_hour: int | None = None,
    channel_id: int | None = None,
    include_hashtags: bool = False,
    video_as_post: bool = True,
    video_description: str | None = None,
) -> VKPublishResult:
    processed = repo.get_processed_post(post_id)
    if processed is None:
        raise PostNotFoundError(f"processed_post {post_id} не найден")

    blocked = check_publish_allowed(
        repo,
        post_id,
        network="vk",
        max_posts_per_day=max_posts_per_day,
        min_interval_minutes=min_interval_minutes,
        max_interval_minutes=max_interval_minutes,
        quiet_start_hour=quiet_start_hour,
        quiet_end_hour=quiet_end_hour,
        channel_id=channel_id,
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
    if processed.video_path and not video_as_post:
        # ТЗ владельца 2026-08-10: пост с видео записью на стене не публикуем — ролик
        # уходит в раздел «Видео» сообщества, вертикальный короткий — в «Клипы».
        video_path = Path(processed.video_path)
        result = publisher.publish_video_only(
            group_id=group_id,
            video_path=video_path,
            title=processed.headline or None,
            description=video_description or text,
            as_clip=_looks_like_clip(video_path),
        )
    else:
        publish_kwargs = dict(group_id=group_id, text=text, image_paths=image_paths)
        if processed.video_path:
            publish_kwargs["video_path"] = Path(processed.video_path)
        result = publisher.publish(**publish_kwargs)

    if result.success:
        repo.mark_published(post_id, "vk", datetime.datetime.utcnow(), vk_post_id=result.post_id)
        logger.info("Пост %d опубликован в VK (post_id=%s)", post_id, result.post_id)
    elif not _already_published(repo, post_id):
        # Не понижаем статус, если пост уже опубликован в другой сети (напр. TG прошёл,
        # а VK упал) — см. тот же фикс в queue_service.py.
        repo.update_processed_post_status(post_id, "failed")

    return result


CLIP_MAX_SECONDS = 180
"""Потолок длительности клипа в VK — ролик длиннее в «Клипы» всё равно не попадёт."""


def _looks_like_clip(video_path: Path) -> bool:
    """Вертикальный и короткий ролик → раздел «Клипы», остальное → «Видео».

    FAIL-SAFE: ffprobe недоступен или файла нет → False, то есть обычная видеозапись.
    Ошибиться в эту сторону дёшево (ролик просто ляжет в «Видео»), в обратную — нет:
    закрытый метод `shortVideo.create` на горизонтальном ролике может отвалиться."""
    try:
        width, height = probe_dimensions(video_path)
        duration = probe_duration_seconds(video_path)
    except Exception:
        logger.info("Не удалось измерить %s — гружу обычной видеозаписью", video_path)
        return False
    return height > width and duration <= CLIP_MAX_SECONDS


def _already_published(repo: Repository, post_id: int) -> bool:
    current = repo.get_processed_post(post_id)
    return current is not None and current.status == "published"


def _build_vk_publish_text(
    headline: str | None,
    rewritten_text: str | None,
    footer_links: FooterLinks | None,
    include_hashtags: bool = False,
) -> str:
    """headline не публикуется отдельной строкой — см. то же решение и обоснование
    в queue_service.py::_build_publish_text."""
    body, hashtags = split_hashtags(rewritten_text or "")

    parts = [strip_markdown(body)]

    if footer_links is not None:
        footer = build_vk_footer(footer_links)
        if footer:
            parts.append(footer)

    if hashtags and include_hashtags:
        parts.append(hashtags)

    return "\n\n".join(parts)
