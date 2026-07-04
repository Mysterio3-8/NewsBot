"""Один цикл проверки всех источников (раздел 7 SPEC.md) — используется headless-режимом
и кнопкой «Проверить сейчас» в UI.
"""
from __future__ import annotations

import logging

from app.config.loader import AppConfig
from app.core.images.providers.base import ImageProvider
from app.core.llm.client import LLMClient
from app.core.monitoring.models import FetchedPost
from app.core.monitoring.telegram_fetcher import TelegramFetcher
from app.core.monitoring.vk_fetcher import VKFetcher
from app.core.pipeline import process_fetched_post
from app.db.models import Source
from app.db.repository import Repository

logger = logging.getLogger("monitoring")


async def run_check_cycle(
    repo: Repository,
    config: AppConfig,
    llm_client: LLMClient,
    *,
    tg_fetcher: TelegramFetcher | None = None,
    vk_fetcher: VKFetcher | None = None,
    image_providers: dict[str, ImageProvider] | None = None,
) -> None:
    if tg_fetcher is not None:
        for source in repo.list_sources(source_type="tg"):
            if not source.enabled:
                continue
            posts = await tg_fetcher.fetch_recent_posts(
                source.url, max_age_hours=config.monitoring.max_post_age_hours
            )
            _process_posts(repo, source, posts, llm_client, config, image_providers)

    if vk_fetcher is not None:
        for source in repo.list_sources(source_type="vk"):
            if not source.enabled:
                continue
            posts = vk_fetcher.fetch_recent_posts(
                int(source.url), max_age_hours=config.monitoring.max_post_age_hours
            )
            _process_posts(repo, source, posts, llm_client, config, image_providers)


def _process_posts(
    repo: Repository,
    source: Source,
    posts: list[FetchedPost],
    llm_client: LLMClient,
    config: AppConfig,
    image_providers: dict[str, ImageProvider] | None,
) -> None:
    for post in posts:
        try:
            process_fetched_post(
                repo,
                source,
                post,
                llm_client=llm_client,
                filters=config.filters,
                scoring_weights=config.scoring_weights,
                rewrite_config=config.rewrite,
                max_post_age_hours=config.monitoring.max_post_age_hours,
                images_config=config.images,
                watermark_config=config.watermark,
                image_providers=image_providers,
            )
        except Exception:
            logger.exception(
                "Ошибка обработки поста %s из источника %s", post.external_id, source.name
            )
