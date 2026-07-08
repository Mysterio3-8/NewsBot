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
            try:
                posts = await _fetch_tg_new_posts(repo, tg_fetcher, source, config)
            except Exception:
                # Один сломанный источник (напр. забаненный VK_USER_TOKEN) не должен
                # рушить весь цикл — иначе публикация вообще не дошла бы до своего шага,
                # который в headless_service идёт ПОСЛЕ run_check_cycle в той же корутине
                # (обнаружено на проде: 6+ часов без публикаций при полной очереди —
                # каждый цикл падал на VK-фетче и никогда не доходил до pick_next_post).
                logger.exception("Не удалось получить посты из источника %s", source.name)
                continue
            logger.info("Источник %s (tg): получено %d новых постов", source.name, len(posts))
            _process_posts(repo, source, posts, llm_client, config, image_providers)
            _advance_tg_cursor(repo, source, posts)

    if vk_fetcher is not None:
        for source in repo.list_sources(source_type="vk"):
            if not source.enabled:
                continue
            try:
                posts = vk_fetcher.fetch_recent_posts(
                    int(source.url),
                    max_age_hours=config.monitoring.max_post_age_hours,
                    known_external_ids=repo.get_existing_external_ids(source.id),
                )
            except Exception:
                logger.exception("Не удалось получить посты из источника %s", source.name)
                continue
            logger.info("Источник %s (vk): получено %d постов", source.name, len(posts))
            _process_posts(repo, source, posts, llm_client, config, image_providers)


def _tg_cursor_key(source_id: int) -> str:
    return f"tg_cursor:{source_id}"


async def _fetch_tg_new_posts(
    repo: Repository,
    tg_fetcher: TelegramFetcher,
    source: Source,
    config: AppConfig,
) -> list[FetchedPost]:
    """Мониторинг по курсору last_processed_message_id (см. fetch_new_posts).
    Курсор берём из settings; если его ещё нет — из максимального external_id в
    истории (плавный переход со старой схемы по времени); если и там пусто — первый
    запуск, инициализируем курсор самым свежим сообщением и ничего не бэкфилим."""
    key = _tg_cursor_key(source.id)
    stored = repo.get_setting(key)
    after_id = int(stored) if stored is not None else repo.get_max_external_id(source.id)

    if after_id is None:
        latest = await tg_fetcher.get_latest_message_id(source.url)
        if latest is not None:
            repo.set_setting(key, str(latest))
        return []

    return await tg_fetcher.fetch_new_posts(
        source.url,
        after_id=after_id,
        limit=config.monitoring.fetch_batch_size,
        known_external_ids=repo.get_existing_external_ids(source.id),
    )


def _advance_tg_cursor(repo: Repository, source: Source, posts: list[FetchedPost]) -> None:
    """Сдвигаем курсор за максимальный обработанный id (в т.ч. отклонённые — они
    «увидены»). Так следующий цикл не перечитывает уже разобранную пачку."""
    if not posts:
        return
    key = _tg_cursor_key(source.id)
    stored = repo.get_setting(key)
    current_id = int(stored) if stored is not None else 0
    new_id = max([current_id, *(int(post.external_id) for post in posts)])
    repo.set_setting(key, str(new_id))


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
                headline_card_config=config.headline_card,
                image_providers=image_providers,
            )
        except Exception:
            logger.exception(
                "Ошибка обработки поста %s из источника %s", post.external_id, source.name
            )
