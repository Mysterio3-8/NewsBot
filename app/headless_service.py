"""24/7 headless-сервис: периодическая проверка источников + автопубликация
(раздел 19.2 SPEC.md).
"""
from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config.loader import AppConfig
from app.core.check_cycle import run_check_cycle
from app.core.llm.client import LLMClient
from app.core.monitoring.telegram_fetcher import TelegramFetcher
from app.core.monitoring.vk_fetcher import VKFetcher
from app.core.publishing.footer import build_footer_links_from_config
from app.core.publishing.queue_service import publish_queued_post
from app.core.publishing.telegram_publisher import TelegramPublisher
from app.core.scheduler import build_triggers, pick_next_post_to_publish
from app.db.repository import Repository
from app.factories import build_telegram_fetcher, build_telegram_publisher, build_vk_fetcher

logger = logging.getLogger("app")


def build_check_job(
    repo: Repository,
    config: AppConfig,
    llm_client: LLMClient,
    tg_fetcher: TelegramFetcher | None,
    vk_fetcher: VKFetcher | None,
):
    async def check_job() -> None:
        await run_check_cycle(
            repo, config, llm_client, tg_fetcher=tg_fetcher, vk_fetcher=vk_fetcher
        )

    return check_job


def build_publish_job(repo: Repository, config: AppConfig, publisher: TelegramPublisher | None):
    footer_links = build_footer_links_from_config(config.footer)

    async def publish_job() -> None:
        if publisher is None:
            return
        post = pick_next_post_to_publish(
            repo,
            max_posts_per_day=config.publishing.schedule.max_posts_per_day,
            important_score_threshold=config.filters.important_score_threshold,
        )
        if post is None:
            return
        await publish_queued_post(
            repo,
            publisher,
            post_id=post.id,
            chat_id=config.publishing.telegram.destination,
            footer_links=footer_links,
        )

    return publish_job


async def run_forever(repo: Repository, config: AppConfig, llm_client: LLMClient) -> None:
    tg_fetcher = build_telegram_fetcher()
    vk_fetcher = build_vk_fetcher()
    tg_publisher = build_telegram_publisher(config)

    if tg_fetcher is None and vk_fetcher is None:
        logger.warning(
            "Ни TG_API_ID/TG_API_HASH, ни VK_USER_TOKEN не заданы — мониторинг источников не запущен"
        )
    if tg_publisher is None:
        logger.warning("TG_BOT_TOKEN не задан — автопубликация недоступна")

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        build_check_job(repo, config, llm_client, tg_fetcher, vk_fetcher),
        IntervalTrigger(minutes=config.monitoring.check_interval_minutes),
    )
    publish_job = build_publish_job(repo, config, tg_publisher)
    for trigger in build_triggers(config.publishing.schedule):
        scheduler.add_job(publish_job, trigger)

    scheduler.start()
    logger.info(
        "Планировщик запущен: проверка источников каждые %d мин",
        config.monitoring.check_interval_minutes,
    )
    await asyncio.Event().wait()  # работает вечно, пока процесс не остановят
