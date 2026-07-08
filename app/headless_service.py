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
from app.db.models import Channel
from app.db.repository import DEFAULT_CHANNEL_NAME, Repository
from app.factories import (
    build_image_providers,
    build_telegram_fetcher,
    build_telegram_publisher_for_channel,
    build_vk_fetcher,
    build_vk_publisher_for_channel,
)

logger = logging.getLogger("app")


def build_cycle_job(
    repo: Repository,
    config: AppConfig,
    llm_client: LLMClient,
    *,
    tg_fetcher: TelegramFetcher | None,
    vk_fetcher: VKFetcher | None,
):
    """Один автономный цикл: собрать свежие посты всех источников → опубликовать посты
    КАЖДОГО канала в его собственные таргеты (мультиканальность). Читалка (fetcher) общая
    для всех каналов, публикация — раздельная: каждый канал в свою VK-группу/TG-канал."""
    image_providers = build_image_providers()
    footer_links = build_footer_links_from_config(config.footer)

    # Publisher'ы кэшируются по имени env-переменной с токеном — чтобы не создавать
    # новый клиент на каждый канал в каждом цикле (несколько каналов могут постить одним
    # ботом/групповым токеном, тогда publisher переиспользуется).
    tg_pub_cache: dict[str, TelegramPublisher | None] = {}
    vk_pub_cache: dict[str, VKPublisher | None] = {}

    def _tg_publisher_for(channel: Channel) -> TelegramPublisher | None:
        if channel.tg_token_env not in tg_pub_cache:
            tg_pub_cache[channel.tg_token_env] = build_telegram_publisher_for_channel(channel)
        return tg_pub_cache[channel.tg_token_env]

    def _vk_publisher_for(channel: Channel) -> VKPublisher | None:
        if channel.vk_token_env not in vk_pub_cache:
            vk_pub_cache[channel.vk_token_env] = build_vk_publisher_for_channel(channel)
        return vk_pub_cache[channel.vk_token_env]

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
        # "публиковать всё сразу"), по порядку появления (oldest-first) — reversed от
        # created_at desc. Каждый канал отдельно → в свои таргеты.
        published_any = False
        for channel in repo.list_channels(enabled_only=True):
            fresh = repo.list_fresh_queued_posts(cutoff, channel_id=channel.id)
            if not fresh:
                continue
            published_any = True
            for post in reversed(fresh):
                await _publish_channel_post(
                    repo,
                    channel,
                    post_id=post.id,
                    config=config,
                    tg_publisher=_tg_publisher_for(channel),
                    vk_publisher=_vk_publisher_for(channel),
                    footer_links=footer_links,
                )
                await asyncio.sleep(random.uniform(3, 8))  # лёгкая пауза между постами

        if not published_any:
            logger.info(_explain_no_post_reason(repo, schedule.max_posts_per_day))

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


async def _publish_channel_post(
    repo: Repository,
    channel: Channel,
    *,
    post_id: int,
    config: AppConfig,
    tg_publisher: TelegramPublisher | None,
    vk_publisher: VKPublisher | None,
    footer_links,
) -> None:
    """Публикует пост в таргеты КОНКРЕТНОГО канала. Сеть пропускается, если у канала не
    задано её назначение (напр. кино/мемы пока только VK, tg_destination пуст) или сеть
    выключена глобально. Статус ('published'/'failed') ставит publish_queued_post*."""
    schedule = config.publishing.schedule
    if tg_publisher is not None and config.publishing.telegram.enabled and channel.tg_destination:
        try:
            await publish_queued_post(
                repo,
                tg_publisher,
                post_id=post_id,
                chat_id=channel.tg_destination,
                footer_links=footer_links,
                max_posts_per_day=schedule.max_posts_per_day,
                min_interval_minutes=schedule.min_interval_minutes,
                include_hashtags=config.rewrite.include_hashtags,
            )
        except Exception:
            logger.exception(
                "Публикация в Telegram не удалась для поста %d (канал %s)", post_id, channel.name
            )

    if vk_publisher is not None and config.publishing.vk.enabled and channel.vk_destination:
        # Небольшая случайная пауза перед второй сетью — чтобы TG и VK не публиковались
        # секунда в секунду (антибан). Короткая (3-8с): режим немедленной публикации.
        delay_seconds = random.uniform(3, 8)
        logger.info("Пауза %.0f сек перед публикацией в VK", delay_seconds)
        await asyncio.sleep(delay_seconds)
        try:
            publish_queued_post_vk(
                repo,
                vk_publisher,
                post_id=post_id,
                group_id=int(channel.vk_destination),
                footer_links=footer_links,
                max_posts_per_day=schedule.max_posts_per_day,
                min_interval_minutes=schedule.min_interval_minutes,
                include_hashtags=config.rewrite.include_hashtags,
            )
        except Exception:
            logger.exception(
                "Публикация в VK не удалась для поста %d (канал %s)", post_id, channel.name
            )


def _sync_default_channel_targets(repo: Repository, config: AppConfig) -> None:
    """Канал 1 (Новости) создан миграцией с пустыми таргетами. Здесь (в headless доступен
    config) заполняем их из глобального config.publishing — новостной канал постит в
    @NewsThreeWord/VK как раньше, без магии fallback в цикле публикации. Не перетираем,
    если таргеты уже заданы (напр. вручную через бота)."""
    default = next(
        (c for c in repo.list_channels() if c.name == DEFAULT_CHANNEL_NAME), None
    )
    if default is None or default.tg_destination or default.vk_destination:
        return
    repo.update_channel(
        default.id,
        tg_token_env=config.publishing.telegram.token_env,
        tg_destination=config.publishing.telegram.destination,
        vk_token_env=config.publishing.vk.token_env,
        vk_destination=config.publishing.vk.destination,
    )


async def run_forever(
    repo: Repository,
    config: AppConfig,
    llm_client: LLMClient,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    tg_fetcher = build_telegram_fetcher()
    vk_fetcher = build_vk_fetcher()
    _sync_default_channel_targets(repo, config)

    if tg_fetcher is None and vk_fetcher is None:
        logger.warning(
            "Ни TG_API_ID/TG_API_HASH, ни VK_USER_TOKEN не заданы — мониторинг источников не запущен"
        )
    if not repo.list_channels(enabled_only=True):
        logger.warning("Нет ни одного включённого канала — автопубликация недоступна")

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
