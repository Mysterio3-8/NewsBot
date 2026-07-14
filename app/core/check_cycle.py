"""Один цикл проверки всех источников (раздел 7 SPEC.md) — используется headless-режимом
и кнопкой «Проверить сейчас» в UI.
"""
from __future__ import annotations

import dataclasses
import logging

from app.config.loader import AppConfig
from app.core.channel_settings import ChannelSettings
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
    settings_cache: dict[int, ChannelSettings] = {}

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
            settings = _channel_settings_for(repo, source, settings_cache)
            _process_posts(repo, source, posts, llm_client, config, image_providers, settings)
            _advance_tg_cursor(repo, source, posts)

    if vk_fetcher is not None:
        for source in repo.list_sources(source_type="vk"):
            if not source.enabled:
                continue
            try:
                posts = vk_fetcher.fetch_recent_posts(
                    int(source.url),
                    max_age_hours=config.monitoring.max_post_age_hours,
                    known_external_ids=repo.get_recent_external_ids(source.id),
                )
            except Exception:
                logger.exception("Не удалось получить посты из источника %s", source.name)
                continue
            logger.info("Источник %s (vk): получено %d постов", source.name, len(posts))
            settings = _channel_settings_for(repo, source, settings_cache)
            _process_posts(repo, source, posts, llm_client, config, image_providers, settings)


def _channel_settings_for(
    repo: Repository, source: Source, cache: dict[int, ChannelSettings]
) -> ChannelSettings:
    """Настройки канала источника (фильтр on/off и т.д.). Источник без канала (channel_id
    None — на проде не бывает после миграции) наследует глобальные дефолты."""
    channel_id = source.channel_id
    if channel_id is None:
        return ChannelSettings()
    if channel_id not in cache:
        channel = repo.get_channel(channel_id)
        cache[channel_id] = (
            ChannelSettings.from_json(channel.settings_json) if channel else ChannelSettings()
        )
    return cache[channel_id]


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
        known_external_ids=repo.get_recent_external_ids(source.id),
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


PHOTO_DESIGN_SETTING = "photo_design_enabled"


def _effective_headline_card(repo: Repository, config: AppConfig):
    """Оформление фото (fade+заголовок) можно включать/выключать из бота — настройка
    photo_design_enabled в БД перекрывает дефолт config.headline_card.enabled. Нет
    настройки → берём дефолт из config.yaml (запрос пользователя 2026-07-11)."""
    raw = repo.get_setting(PHOTO_DESIGN_SETTING)
    if raw is None:
        return config.headline_card
    return dataclasses.replace(config.headline_card, enabled=(raw == "1"))


def _process_posts(
    repo: Repository,
    source: Source,
    posts: list[FetchedPost],
    llm_client: LLMClient,
    config: AppConfig,
    image_providers: dict[str, ImageProvider] | None,
    settings: ChannelSettings,
) -> None:
    headline_card = _effective_headline_card(repo, config)
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
                filters_enabled=settings.filters_enabled,
                images_config=config.images,
                watermark_config=config.watermark,
                headline_card_config=headline_card,
                image_providers=image_providers,
                image_query_mode=settings.image_query_mode,
                image_search_providers=settings.image_providers_order,
            )
        except Exception:
            logger.exception(
                "Ошибка обработки поста %s из источника %s", post.external_id, source.name
            )
