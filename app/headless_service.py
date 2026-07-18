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
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config.loader import AppConfig
from app.core.channel_settings import ChannelSettings
from app.core.check_cycle import run_check_cycle
from app.core.llm.client import LLMClient
from app.core.monitoring.telegram_fetcher import TelegramFetcher
from app.core.monitoring.vk_fetcher import VKFetcher
from app.core.publishing.circuit_breaker import CircuitBreaker
from app.core.publishing.footer import FooterLinks, build_footer_links_from_config
from app.core.publishing.queue_service import publish_queued_post
from app.core.publishing.telegram_publisher import TelegramPublisher
from app.core.publishing.token_bucket import TokenBucket
from app.core.publishing.vk_errors import VKErrorClass, classify_vk_code
from app.core.publishing.vk_publisher import VKPublisher, VKPublishResult
from app.core.publishing.vk_queue_service import publish_queued_post_vk
from app.core.publishing.weekly_repost import repost_best_post
from app.core.video.daily_video_repost import publish_due_clips, run_daily_video_repost
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
    vk_token_bucket: TokenBucket | None = None,
    vk_cooldown_bucket: TokenBucket | None = None,
):
    """Один автономный цикл: собрать свежие посты всех источников → опубликовать посты
    КАЖДОГО канала в его собственные таргеты (мультиканальность). Читалка (fetcher) общая
    для всех каналов, публикация — раздельная: каждый канал в свою VK-группу/TG-канал."""
    image_providers = build_image_providers()
    footer_links = build_footer_links_from_config(config.footer)
    # Circuit breaker общий на весь цикл: при серии ошибок VK (rate limit/бан) открывает
    # паузу на сеть+токен, не давая долбить упавший API (антибан). Состояние в БД —
    # переживает рестарт сервиса.
    breaker = CircuitBreaker(repo)

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
            vk_pub_cache[channel.vk_token_env] = build_vk_publisher_for_channel(
                channel, token_bucket=vk_token_bucket, cooldown_bucket=vk_cooldown_bucket
            )
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
                    breaker=breaker,
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
    breaker: CircuitBreaker,
) -> None:
    """Публикует пост в таргеты КОНКРЕТНОГО канала. Сеть пропускается, если у канала не
    задано её назначение (напр. кино/мемы пока только VK, tg_destination пуст) или сеть
    выключена глобально. Статус ('published'/'failed') ставит publish_queued_post*.
    Лимит публикаций и счётчик — per-channel (защита от бана + изоляция каналов): у канала
    свой max_posts_per_day (ChannelSettings) и свой счётчик за сутки (channel_id)."""
    schedule = config.publishing.schedule
    settings = ChannelSettings.from_json(channel.settings_json)
    max_per_day = (
        settings.max_posts_per_day
        if settings.max_posts_per_day is not None
        else schedule.max_posts_per_day
    )
    min_interval = (
        settings.min_interval_minutes
        if settings.min_interval_minutes is not None
        else schedule.min_interval_minutes
    )
    # Свой футер канала (ссылка в конце, напр. на TG-канал кино) переопределяет глобальный.
    # telegram_signature — брендовая подпись для TG-рендера; VK/IG получат призыв
    # подписаться + этот же tg_footer_url (см. footer.build_vk_footer).
    channel_footer = (
        FooterLinks(telegram_signature="🎬 Больше фильмов", telegram_url=settings.tg_footer_url)
        if settings.tg_footer_url
        else footer_links
    )
    if tg_publisher is not None and config.publishing.telegram.enabled and channel.tg_destination:
        try:
            await publish_queued_post(
                repo,
                tg_publisher,
                post_id=post_id,
                chat_id=channel.tg_destination,
                footer_links=channel_footer,
                max_posts_per_day=max_per_day,
                min_interval_minutes=min_interval,
                channel_id=channel.id,
                include_hashtags=config.rewrite.include_hashtags,
            )
        except Exception:
            logger.exception(
                "Публикация в Telegram не удалась для поста %d (канал %s)", post_id, channel.name
            )

    if vk_publisher is not None and config.publishing.vk.enabled and channel.vk_destination:
        vk_token_env = channel.vk_token_env or config.publishing.vk.token_env
        if breaker.is_open("vk", vk_token_env):
            logger.warning(
                "Публикация в VK пропущена для поста %d (канал %s): circuit breaker открыт (%s)",
                post_id, channel.name, vk_token_env,
            )
            return
        # Небольшая случайная пауза перед второй сетью — чтобы TG и VK не публиковались
        # секунда в секунду (антибан). Короткая (3-8с): режим немедленной публикации.
        delay_seconds = random.uniform(3, 8)
        logger.info("Пауза %.0f сек перед публикацией в VK", delay_seconds)
        await asyncio.sleep(delay_seconds)
        try:
            result = publish_queued_post_vk(
                repo,
                vk_publisher,
                post_id=post_id,
                group_id=int(channel.vk_destination),
                footer_links=channel_footer,
                max_posts_per_day=max_per_day,
                min_interval_minutes=min_interval,
                channel_id=channel.id,
                include_hashtags=config.rewrite.include_hashtags,
            )
            _record_vk_breaker(breaker, vk_token_env, result)
        except Exception:
            logger.exception(
                "Публикация в VK не удалась для поста %d (канал %s)", post_id, channel.name
            )
            breaker.record_failure("vk", vk_token_env, VKErrorClass.TRANSIENT)


def _record_vk_breaker(
    breaker: CircuitBreaker, token_env: str, result: VKPublishResult
) -> None:
    """Успех закрывает цепь, реальный сбой VK — двигает breaker к паузе. Отказ
    rate_guard (throttled) — НЕ сетевая ошибка, breaker не трогаем."""
    if result.success:
        breaker.record_success("vk", token_env)
        return
    if result.error and result.error.startswith("throttled:"):
        return
    breaker.record_failure("vk", token_env, classify_vk_code(result.error_code))


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


def build_weekly_repost_job(repo: Repository, config: AppConfig, vk_fetcher: VKFetcher | None):
    """Раз в неделю: для каждого канала с weekly_repost — перезалить лучший пост за
    7 дней (по просмотрам+лайкам VK). Запрос пользователя 2026-07-14 (Кино). Вовлечённость
    читается личным токеном (vk_fetcher) — групповым wall.getById недоступен (VK [27])."""
    footer_links = build_footer_links_from_config(config.footer)

    async def weekly_job() -> None:
        for channel in repo.list_channels(enabled_only=True):
            settings = ChannelSettings.from_json(channel.settings_json)
            if not settings.weekly_repost:
                continue
            channel_footer = (
                FooterLinks(telegram_signature="🎬 Больше фильмов", telegram_url=settings.tg_footer_url)
                if settings.tg_footer_url
                else footer_links
            )
            try:
                await repost_best_post(
                    repo,
                    channel,
                    tg_publisher=build_telegram_publisher_for_channel(channel),
                    vk_publisher=build_vk_publisher_for_channel(channel),
                    vk_fetcher=vk_fetcher,
                    footer_links=channel_footer,
                )
            except Exception:
                logger.exception("Еженедельный репост канала %s упал", channel.name)

    return weekly_job


def build_daily_video_job(
    repo: Repository,
    config: AppConfig,
    llm_client: LLMClient,
    vk_fetcher: VKFetcher | None,
    *,
    vk_token_bucket: TokenBucket | None = None,
    vk_cooldown_bucket: TokenBucket | None = None,
):
    """Раз в день: для каналов с daily_video_group — репост одного видео из группы-
    источника + нарезка клипов (ТЗ 2026-07-18, Кино). Скачивание/ffmpeg — блокирующие
    и долгие, уводим в поток, чтобы не вешать цикл проверки/публикации."""
    footer_links = build_footer_links_from_config(config.footer)

    async def daily_video_job() -> None:
        if vk_fetcher is None:
            logger.warning("Видео-репост пропущен: VK_USER_TOKEN не задан (нет фетчера)")
            return
        for channel in repo.list_channels(enabled_only=True):
            settings = ChannelSettings.from_json(channel.settings_json)
            if settings.daily_video_group is None:
                continue
            publisher = build_vk_publisher_for_channel(
                channel, token_bucket=vk_token_bucket, cooldown_bucket=vk_cooldown_bucket
            )
            if publisher is None:
                logger.warning("Видео-репост [%s]: VK publisher недоступен", channel.name)
                continue
            channel_footer = (
                FooterLinks(telegram_signature="🎬 Больше фильмов", telegram_url=settings.tg_footer_url)
                if settings.tg_footer_url
                else footer_links
            )
            try:
                await asyncio.to_thread(
                    run_daily_video_repost,
                    repo,
                    channel,
                    vk_fetcher=vk_fetcher,
                    vk_publisher=publisher,
                    llm_client=llm_client,
                    footer_links=channel_footer,
                )
            except Exception:
                logger.exception("Видео-репост канала %s упал", channel.name)

    return daily_video_job


def build_clip_publish_job(
    repo: Repository,
    *,
    vk_token_bucket: TokenBucket | None = None,
    vk_cooldown_bucket: TokenBucket | None = None,
):
    """Каждые 10 минут: опубликовать клипы, чьё запланированное время пришло. План — в
    БД (clip_segments), поэтому рестарт сервиса не теряет расписание клипов."""

    async def clip_publish_job() -> None:
        def publisher_for(channel: Channel):
            return build_vk_publisher_for_channel(
                channel, token_bucket=vk_token_bucket, cooldown_bucket=vk_cooldown_bucket
            )

        await asyncio.to_thread(publish_due_clips, repo, vk_publisher_for=publisher_for)

    return clip_publish_job


async def run_forever(
    repo: Repository,
    config: AppConfig,
    llm_client: LLMClient,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    tg_fetcher = build_telegram_fetcher()
    # Два лимитера на весь процесс, общие для чтения (VKFetcher, VK_USER_TOKEN) и
    # публикации медиа (VKPublisher, VK_PHOTO_UPLOAD_TOKEN — часто тот же личный
    # аккаунт): vk_token_bucket — технический burst-лимит ≤2 запр/сек (правило VK,
    # нужен внутри одной загрузки). vk_cooldown_bucket — ЖЁСТКИЙ лимит НА ОПЕРАЦИЮ
    # личного токена (явный запрос пользователя 2026-07-10: "даже если публикация
    # будет идти через 10 минут, главное бана избежать") — config.antiban,
    # 0 отключает. Групповой токен (публикация текста) этим не ограничен.
    vk_token_bucket = TokenBucket()
    cooldown_seconds = config.antiban.vk_personal_token_cooldown_seconds
    vk_cooldown_bucket = TokenBucket(1 / cooldown_seconds) if cooldown_seconds > 0 else None
    vk_fetcher = build_vk_fetcher(token_bucket=vk_token_bucket, cooldown_bucket=vk_cooldown_bucket)
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
            vk_token_bucket=vk_token_bucket,
            vk_cooldown_bucket=vk_cooldown_bucket,
        ),
        IntervalTrigger(minutes=config.monitoring.check_interval_minutes, jitter=jitter_seconds),
        # Без next_run_time IntervalTrigger ждёт первый полный интервал (до 4 часов)
        # прежде чем сделать хоть что-то — при нажатии "Старт" пользователь ждёт
        # первую проверку сразу, а не через несколько часов. Антиспам-стопор
        # (rate_guard) всё равно проверяется внутри publish_queued_post*, так что
        # немедленный первый цикл не обходит никакие ограничения на публикацию.
        next_run_time=datetime.datetime.now(),
    )
    # Еженедельный репост лучшего поста (каналы с weekly_repost, напр. Кино). Первый
    # запуск — через неделю (не сразу, чтобы накопилась статистика вовлечённости).
    scheduler.add_job(
        build_weekly_repost_job(repo, config, vk_fetcher),
        IntervalTrigger(weeks=1),
    )
    # Ежедневный видео-репост (каналы с daily_video_group, напр. Кино): cron 08:00 UTC
    # (11:00 МСК) + джиттер до 2ч — время публикации случайное в окне 11:00-13:00 МСК.
    scheduler.add_job(
        build_daily_video_job(
            repo, config, llm_client, vk_fetcher,
            vk_token_bucket=vk_token_bucket, vk_cooldown_bucket=vk_cooldown_bucket,
        ),
        CronTrigger(hour=8, minute=0, jitter=2 * 3600),
    )
    # Публикатор клипов: каждые 10 мин постит клипы, чьё запланированное время пришло.
    scheduler.add_job(
        build_clip_publish_job(
            repo, vk_token_bucket=vk_token_bucket, vk_cooldown_bucket=vk_cooldown_bucket
        ),
        IntervalTrigger(minutes=10),
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
