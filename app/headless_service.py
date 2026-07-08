"""24/7 headless-сервис (раздел 19.2 SPEC.md).

Автономный режим: каждые `check_interval_minutes` один цикл — проверка источников →
обработка → немедленная публикация одного лучшего поста сразу в Telegram и VK (с
небольшой случайной паузой между сетями). Реальный темп публикации держит
`publishing.schedule.min_interval_minutes`/`max_posts_per_day` (rate_guard) — по
умолчанию 12 постов в сутки, по одному примерно каждые 2 часа, без пачки.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import random

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
from app.core.publishing.vk_publisher import VKPublisher
from app.core.publishing.vk_queue_service import publish_queued_post_vk
from app.core.scheduler import start_of_today_utc
from app.db.repository import Repository
from app.factories import (
    build_image_providers,
    build_telegram_fetcher,
    build_telegram_publisher,
    build_vk_fetcher,
    build_vk_publisher,
)

logger = logging.getLogger("app")


def build_cycle_job(
    repo: Repository,
    config: AppConfig,
    llm_client: LLMClient,
    *,
    tg_fetcher: TelegramFetcher | None,
    vk_fetcher: VKFetcher | None,
    tg_publisher: TelegramPublisher | None,
    vk_publisher: VKPublisher | None,
):
    """Один автономный цикл: собрать свежие посты → опубликовать один лучший в обе сети."""
    image_providers = build_image_providers()
    footer_links = build_footer_links_from_config(config.footer)

    async def cycle_job() -> None:
        await run_check_cycle(
            repo,
            config,
            llm_client,
            tg_fetcher=tg_fetcher,
            vk_fetcher=vk_fetcher,
            image_providers=image_providers,
        )

        schedule = config.publishing.schedule
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=schedule.publish_freshness_hours)
        repo.expire_stale_queued_posts(cutoff)

        # Публикуем ВСЕ свежие посты очереди за цикл (запрос пользователя 2026-07-07:
        # "публиковать всё сразу, без лимита"), по порядку появления (oldest-first),
        # а не один самый свежий, как раньше — из-за этого пачка постов терялась.
        # list_fresh_queued_posts отдаёт created_at desc → reversed = хронология.
        fresh = repo.list_fresh_queued_posts(cutoff)
        if not fresh:
            logger.info(_explain_no_post_reason(repo, schedule.max_posts_per_day))
            return

        for post in reversed(fresh):
            await _publish_to_all(
                repo,
                post_id=post.id,
                config=config,
                tg_publisher=tg_publisher,
                vk_publisher=vk_publisher,
                footer_links=footer_links,
            )
            await asyncio.sleep(random.uniform(3, 8))  # лёгкая пауза между постами

    return cycle_job


def _explain_no_post_reason(repo: Repository, max_posts_per_day: int) -> str:
    """Раньше при post is None писалось только "нет постов к публикации" — приходилось
    руками лезть в БД, чтобы понять, пуста ли очередь или упёрлись в дневной лимит.
    Теперь причина видна сразу в логе."""
    published_today = repo.count_published_since(start_of_today_utc())
    if published_today >= max_posts_per_day:
        return f"Публикация пропущена: дневной лимит достигнут ({published_today}/{max_posts_per_day})"
    queued_count = len(repo.list_processed_posts(status="queued"))
    if queued_count == 0:
        return "Публикация пропущена: очередь пуста, новых постов, прошедших фильтры, не найдено"
    return f"Публикация пропущена: в очереди {queued_count} постов, но ни один не выбран (неожиданно)"


async def _publish_to_all(
    repo: Repository,
    *,
    post_id: int,
    config: AppConfig,
    tg_publisher: TelegramPublisher | None,
    vk_publisher: VKPublisher | None,
    footer_links,
) -> None:
    """Публикует в обе сети. Статус поста ('published'/'failed') ставит publish_queued_post*,
    поэтому в БД он опубликован, если прошла хотя бы одна сеть — этого достаточно, чтобы
    не публиковать его снова в следующем цикле."""
    schedule = config.publishing.schedule
    if tg_publisher is not None and config.publishing.telegram.enabled:
        try:
            await publish_queued_post(
                repo,
                tg_publisher,
                post_id=post_id,
                chat_id=config.publishing.telegram.destination,
                footer_links=footer_links,
                max_posts_per_day=schedule.max_posts_per_day,
                min_interval_minutes=schedule.min_interval_minutes,
                include_hashtags=config.rewrite.include_hashtags,
            )
        except Exception:
            logger.exception("Публикация в Telegram не удалась для поста %d", post_id)

    if vk_publisher is not None and config.publishing.vk.enabled:
        # Небольшая случайная пауза перед второй сетью — чтобы TG и VK не публиковались
        # секунда в секунду (антибан). Короткая (3-8с): режим немедленной публикации,
        # длинная пауза 1-5 мин мешала бы публиковать пачку постов «сразу».
        delay_seconds = random.uniform(3, 8)
        logger.info("Пауза %.0f сек перед публикацией в VK", delay_seconds)
        await asyncio.sleep(delay_seconds)
        try:
            publish_queued_post_vk(
                repo,
                vk_publisher,
                post_id=post_id,
                group_id=int(config.publishing.vk.destination),
                footer_links=footer_links,
                max_posts_per_day=schedule.max_posts_per_day,
                min_interval_minutes=schedule.min_interval_minutes,
                include_hashtags=config.rewrite.include_hashtags,
            )
        except Exception:
            logger.exception("Публикация в VK не удалась для поста %d", post_id)


async def run_forever(
    repo: Repository,
    config: AppConfig,
    llm_client: LLMClient,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    tg_fetcher = build_telegram_fetcher()
    vk_fetcher = build_vk_fetcher()
    tg_publisher = build_telegram_publisher(config)
    vk_publisher = build_vk_publisher(config)

    if tg_fetcher is None and vk_fetcher is None:
        logger.warning(
            "Ни TG_API_ID/TG_API_HASH, ни VK_USER_TOKEN не заданы — мониторинг источников не запущен"
        )
    if tg_publisher is None and vk_publisher is None:
        logger.warning("Ни TG_BOT_TOKEN, ни VK_GROUP_TOKEN не заданы — автопубликация недоступна")

    scheduler = AsyncIOScheduler()
    # jitter — случайный разброс момента запуска (±jitter_minutes), чтобы публикации
    # не выходили робото-ровно в HH:00 и меньше походили на бота (антибан).
    jitter_seconds = config.publishing.schedule.jitter_minutes * 60
    scheduler.add_job(
        build_cycle_job(
            repo,
            config,
            llm_client,
            tg_fetcher=tg_fetcher,
            vk_fetcher=vk_fetcher,
            tg_publisher=tg_publisher,
            vk_publisher=vk_publisher,
        ),
        IntervalTrigger(minutes=config.monitoring.check_interval_minutes, jitter=jitter_seconds),
        # Без next_run_time IntervalTrigger ждёт первый полный интервал (до 4 часов)
        # прежде чем сделать хоть что-то — при нажатии "Старт" пользователь ждёт
        # первую проверку сразу, а не через несколько часов. Антиспам-стопор
        # (rate_guard) всё равно проверяется внутри publish_queued_post*, так что
        # немедленный первый цикл не обходит никакие ограничения на публикацию.
        next_run_time=datetime.datetime.now(),
    )

    scheduler.start()
    logger.info(
        "Автономный режим запущен: цикл (проверка + публикация) каждые %d мин",
        config.monitoring.check_interval_minutes,
    )
    try:
        # stop_event позволяет веб-лаунчеру (app/web_launcher.py) остановить цикл
        # кнопкой "Стоп" — без него сервис работал бы вечно, пока не убьют процесс.
        await (stop_event or asyncio.Event()).wait()
    finally:
        scheduler.shutdown(wait=False)
