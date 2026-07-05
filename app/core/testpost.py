"""Ручной тестовый пост по конкретной VK-ссылке — обходит скоринг/фильтры (это
осознанный ручной тест конкретного контента: проверить рерайт/фото/вотермарк на
выбранном примере, а не решить, достойна ли новость публикации), но НЕ обходит
антиспам-стопор rate_guard — это жёсткий инвариант (см. CLAUDE.md)."""
from __future__ import annotations

import json
import logging
import re
import time

from app.config.loader import AppConfig
from app.core.llm.client import LLMClient
from app.core.llm.headline_generator import generate_headlines
from app.core.llm.rewriter import rewrite_post
from app.core.monitoring.vk_fetcher import VKFetcher, vk_post_to_fetched_post
from app.core.pipeline import _prepare_images
from app.core.publishing.footer import build_footer_links_from_config
from app.core.publishing.queue_service import publish_queued_post
from app.core.publishing.vk_queue_service import publish_queued_post_vk
from app.db.repository import Repository
from app.factories import (
    build_image_providers,
    build_telegram_publisher,
    build_vk_fetcher,
    build_vk_publisher,
)

logger = logging.getLogger("app")

TEST_SOURCE_NAME = "test_manual"

_WALL_RE = re.compile(r"wall(-?\d+)_(\d+)")
_PHOTO_RE = re.compile(r"photo(-?\d+)_(\d+)")


def parse_vk_post_ref(text: str) -> tuple[int, int] | None:
    """Достаёт (owner_id, post_id) из обычной ссылки на пост ('wall<owner>_<post>',
    так выглядит большинство vk.com/vk.ru ссылок). Глубокие ссылки на фото внутри
    поста ('photo<owner>_<photo_id>') сюда не попадают — post_id для них не в самой
    ссылке, его нужно разрешать через photos.getById, см. resolve_post_ref_from_photo."""
    match = _WALL_RE.search(text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def extract_photo_ref(text: str) -> tuple[int, int] | None:
    """Достаёт (owner_id, photo_id) из глубокой ссылки вида 'photo<owner>_<photo_id>'
    (напр. vk.ru/group?z=photo-152992737_457687164) — сам owner/post_id поста, к
    которому привязано фото, отдельно резолвится через photos.getById."""
    match = _PHOTO_RE.search(text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def resolve_post_ref_from_photo(fetcher: VKFetcher, owner_id: int, photo_id: int) -> tuple[int, int] | None:
    photos = fetcher._api.photos.getById(photos=f"{owner_id}_{photo_id}")
    if not photos:
        return None
    photo = photos[0]
    post_id = photo.get("post_id")
    if not post_id:
        return None
    return photo["owner_id"], post_id


def get_or_create_test_source(repo: Repository):
    """Отдельный псевдо-источник для ручных тестовых постов — не в списке реальных
    каналов пользователя, всегда enabled=False, чтобы штатный check_cycle его никогда
    не подхватил сам."""
    for source in repo.list_sources(source_type="vk"):
        if source.name == TEST_SOURCE_NAME:
            return source
    source = repo.create_source(type="vk", name=TEST_SOURCE_NAME, url="0", priority=1)
    repo.update_source(source.id, enabled=False)
    return repo.get_source(source.id)


async def test_post_now(
    repo: Repository,
    config: AppConfig,
    llm_client: LLMClient,
    *,
    vk_ref: str,
) -> str:
    fetcher = build_vk_fetcher()
    if fetcher is None:
        return "VK_USER_TOKEN не задан — тестовый пост по VK-ссылке недоступен."

    ref = parse_vk_post_ref(vk_ref)
    if ref is None:
        photo_ref = extract_photo_ref(vk_ref)
        if photo_ref is None:
            return (
                "Не понял ссылку. Нужна ссылка на пост (vk.com/wall-123_456) "
                "или на фото внутри поста (vk.ru/group?z=photo-123_456)."
            )
        try:
            resolved = resolve_post_ref_from_photo(fetcher, *photo_ref)
        except Exception as error:
            return f"Не удалось получить фото по ссылке: {error}"
        if resolved is None:
            return "У этого фото нет привязанного поста на стене."
        owner_id, post_id = resolved
    else:
        owner_id, post_id = ref

    try:
        wall_items = fetcher._api.wall.getById(posts=f"{owner_id}_{post_id}")
    except Exception as error:
        return f"Не удалось получить пост: {error}"
    if not wall_items:
        return "Пост не найден (удалён или закрытая группа)."

    fetched = vk_post_to_fetched_post(wall_items[0])
    external_id = f"test_{owner_id}_{post_id}_{int(time.time())}"
    local_media = fetcher._download_photos(external_id, fetched.media_urls)

    source = get_or_create_test_source(repo)
    raw_post = repo.create_raw_post(
        source_id=source.id,
        external_id=external_id,
        raw_text=fetched.text,
        media=json.dumps(local_media) if local_media else None,
    )

    rewritten = rewrite_post(
        llm_client,
        text=fetched.text,
        source="тест",
        style=config.rewrite.style,
        max_length=min(config.rewrite.max_length_chars, max(len(fetched.text), 1)),
        include_hashtags=config.rewrite.include_hashtags,
    )
    headlines = generate_headlines(
        llm_client,
        text=rewritten,
        style=config.rewrite.style,
        count=config.rewrite.headline_variants,
    )
    headline = headlines[0] if headlines else None

    image_paths = _prepare_images(
        llm_client,
        raw_post_id=raw_post.id,
        post_media_urls=local_media,
        rewritten_text=rewritten,
        headline=headline,
        images_config=config.images,
        watermark_config=config.watermark,
        headline_card_config=config.headline_card,
        image_providers=build_image_providers(),
    )

    processed = repo.create_processed_post(
        raw_post_id=raw_post.id,
        score=100.0,
        category="тест",
        rewritten_text=rewritten,
        headline=headline,
        image_paths=image_paths,
        video_path=None,
        status="queued",
    )

    return await _publish_test_post(repo, config, processed.id)


async def _publish_test_post(repo: Repository, config: AppConfig, post_id: int) -> str:
    footer_links = build_footer_links_from_config(config.footer)
    schedule = config.publishing.schedule
    results: list[str] = []

    tg_publisher = build_telegram_publisher(config)
    if tg_publisher is not None and config.publishing.telegram.enabled:
        result = await publish_queued_post(
            repo, tg_publisher, post_id=post_id,
            chat_id=config.publishing.telegram.destination,
            footer_links=footer_links,
            max_posts_per_day=schedule.max_posts_per_day,
            min_interval_minutes=schedule.min_interval_minutes,
            include_hashtags=config.rewrite.include_hashtags,
        )
        results.append("TG: ✅" if result.success else f"TG: ❌ {result.error}")

    vk_publisher = build_vk_publisher(config)
    if vk_publisher is not None and config.publishing.vk.enabled:
        result = publish_queued_post_vk(
            repo, vk_publisher, post_id=post_id,
            group_id=int(config.publishing.vk.destination),
            footer_links=footer_links,
            max_posts_per_day=schedule.max_posts_per_day,
            min_interval_minutes=schedule.min_interval_minutes,
            include_hashtags=config.rewrite.include_hashtags,
        )
        results.append("VK: ✅" if result.success else f"VK: ❌ {result.error}")

    if not results:
        return f"Тестовый пост создан (id={post_id}), но публикаторы не настроены."
    return f"Тестовый пост (id={post_id}):\n" + "\n".join(results)
