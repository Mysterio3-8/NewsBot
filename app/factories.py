"""Сборка сервисов из config.yaml + .env. Используется и UI, и headless-режимом."""
from __future__ import annotations

import datetime
import logging
import os

from app.config.loader import AppConfig
from app.core.channel_settings import ChannelSettings
from app.core.images.providers.base import ImageProvider
from app.core.publishing import token_balancer
from app.db.models import Channel
from app.core.images.providers.google_provider import GoogleImageProvider
from app.core.images.providers.pexels_provider import PexelsProvider
from app.core.images.providers.pixabay_provider import PixabayProvider
from app.core.images.providers.unsplash_provider import UnsplashProvider
from app.core.monitoring.telegram_fetcher import TelegramFetcher
from app.core.monitoring.vk_fetcher import VKFetcher
from app.core.publishing.telegram_publisher import TelegramPublisher
from app.core.publishing.telethon_video_publisher import TelethonVideoPublisher
from app.core.publishing.token_bucket import TokenBucket
from app.core.publishing.vk_publisher import VKPublisher
from app.core.publishing.youtube_publisher import YouTubeCredentials, YouTubePublisher

logger = logging.getLogger("app")


def build_telegram_publisher(config: AppConfig) -> TelegramPublisher | None:
    bot_token = os.environ.get(config.publishing.telegram.token_env)
    if not bot_token:
        return None
    return TelegramPublisher(bot_token)


def build_vk_publisher(
    config: AppConfig,
    *,
    token_bucket: TokenBucket | None = None,
    cooldown_bucket: TokenBucket | None = None,
) -> VKPublisher | None:
    group_token = os.environ.get(config.publishing.vk.token_env)
    if not group_token:
        return None
    # Опционально: личный токен админа группы ТОЛЬКО для загрузки фото/видео —
    # group-токен не может (VK error 27, см. CLAUDE.md). wall.post всегда идёт через
    # group_token. Не задан — best-effort продолжает публиковать текстом без вложения.
    upload_token = os.environ.get("VK_PHOTO_UPLOAD_TOKEN")
    return VKPublisher(
        group_token,
        upload_token=upload_token,
        token_bucket=token_bucket,
        cooldown_bucket=cooldown_bucket,
    )


def build_telegram_publisher_for_channel(channel: Channel) -> TelegramPublisher | None:
    """Publisher канала: токен бота берётся из env-переменной, ИМЯ которой хранится в
    channel.tg_token_env (сам токен — в .env, инвариант). Разные каналы могут постить
    одним ботом (одно имя env) в разные @назначения или разными ботами."""
    bot_token = os.environ.get(channel.tg_token_env)
    if not bot_token:
        return None
    return TelegramPublisher(bot_token)


def build_vk_publisher_for_channel(
    channel: Channel,
    *,
    token_bucket: TokenBucket | None = None,
    cooldown_bucket: TokenBucket | None = None,
    repo=None,
) -> VKPublisher | None:
    """Publisher канала: групповой токен из channel.vk_token_env + опц. личный upload-
    токен из channel.vk_upload_token_env (group-токен не грузит медиа, VK error 27).

    Если у канала задан ПУЛ личных токенов (settings.vk_upload_token_envs) и передан
    repo — upload-токен выбирает балансер (наименее нагруженный сегодня), чтобы объём
    не выжигал один токен. Без пула/repo поведение прежнее — один токен."""
    group_token = os.environ.get(channel.vk_token_env)
    if not group_token:
        return None
    upload_env = pick_upload_token_env(channel, repo)
    upload_token = os.environ.get(upload_env) if upload_env else None
    return VKPublisher(
        group_token,
        upload_token=upload_token,
        token_bucket=token_bucket,
        cooldown_bucket=cooldown_bucket,
    )


VK_TOKEN_USAGE_SETTING = "vk_token_usage"


def pick_upload_token_env(channel: Channel, repo=None) -> str | None:
    """Какой личный токен использовать для загрузки медиа этого канала. Пул задан и есть
    repo → балансер (наименее нагруженный сегодня, счётчик пишется сразу). Иначе —
    одиночный channel.vk_upload_token_env, как было."""
    settings = ChannelSettings.from_json(channel.settings_json)
    pool = settings.vk_upload_token_envs
    if not pool or repo is None:
        return channel.vk_upload_token_env

    now = datetime.datetime.utcnow()
    states = token_balancer.load_states(repo.get_setting(VK_TOKEN_USAGE_SETTING))
    cap = settings.vk_token_daily_cap or token_balancer.DEFAULT_PER_TOKEN_DAILY_CAP
    chosen = token_balancer.pick_token(pool, states, now=now, per_token_daily_cap=cap)
    if chosen is None:
        logger.warning(
            "Балансер: все личные токены канала «%s» исчерпаны/в кулдауне — медиа не грузим",
            channel.name,
        )
        return None
    repo.set_setting(
        VK_TOKEN_USAGE_SETTING,
        token_balancer.dump_states(token_balancer.record_use(states, chosen, now)),
    )
    logger.info("Балансер: канал «%s» грузит медиа токеном %s", channel.name, chosen)
    return chosen


def build_telegram_fetcher() -> TelegramFetcher | None:
    api_id = os.environ.get("TG_API_ID")
    api_hash = os.environ.get("TG_API_HASH")
    if not api_id or not api_hash:
        return None
    session_name = os.environ.get("TG_SESSION_NAME", "news_rewriter_session")
    return TelegramFetcher(api_id=int(api_id), api_hash=api_hash, session_name=session_name)


def build_youtube_publisher() -> YouTubePublisher | None:
    """YouTube-загрузчик из .env. Все три ключа обязательны — иначе None (загрузка на
    YouTube просто не идёт, остальные сети работают). Приватность — YT_UPLOAD_PRIVACY
    (public/unlisted/private), по умолчанию public."""
    client_id = os.environ.get("YT_UPLOAD_CLIENT_ID")
    client_secret = os.environ.get("YT_UPLOAD_CLIENT_SECRET")
    refresh_token = os.environ.get("YT_UPLOAD_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        return None
    credentials = YouTubeCredentials(
        client_id=client_id, client_secret=client_secret, refresh_token=refresh_token
    )
    privacy = os.environ.get("YT_UPLOAD_PRIVACY", "public")
    return YouTubePublisher(credentials, privacy=privacy)


def build_telethon_video_publisher() -> TelethonVideoPublisher | None:
    """Заливка фильма в TG идёт пользовательской сессией (Bot API не берёт >50 МБ) —
    те же TG_API_ID/TG_API_HASH, что и у читалки, но своя копия файла сессии."""
    api_id = os.environ.get("TG_API_ID")
    api_hash = os.environ.get("TG_API_HASH")
    if not api_id or not api_hash:
        return None
    session_name = os.environ.get("TG_SESSION_NAME", "news_rewriter_session")
    return TelethonVideoPublisher(
        api_id=int(api_id), api_hash=api_hash, session_name=session_name
    )


def build_vk_fetcher(
    *,
    token_bucket: TokenBucket | None = None,
    cooldown_bucket: TokenBucket | None = None,
) -> VKFetcher | None:
    user_token = os.environ.get("VK_USER_TOKEN")
    if not user_token:
        return None
    return VKFetcher(user_token, token_bucket=token_bucket, cooldown_bucket=cooldown_bucket)


def build_image_providers() -> dict[str, ImageProvider]:
    """Только сток-провайдеры с заданным ключом — "source" (фото из самого поста)
    собирается отдельно на каждый пост в pipeline.py, т.к. зависит от конкретного поста."""
    providers: dict[str, ImageProvider] = {}

    unsplash_key = os.environ.get("UNSPLASH_ACCESS_KEY")
    if unsplash_key:
        providers["unsplash"] = UnsplashProvider(unsplash_key)

    pexels_key = os.environ.get("PEXELS_API_KEY")
    if pexels_key:
        providers["pexels"] = PexelsProvider(pexels_key)

    pixabay_key = os.environ.get("PIXABAY_API_KEY")
    if pixabay_key:
        providers["pixabay"] = PixabayProvider(pixabay_key)

    google_key = os.environ.get("GOOGLE_CSE_KEY")
    google_cx = os.environ.get("GOOGLE_CSE_ID")
    if google_key and google_cx:
        providers["google"] = GoogleImageProvider(google_key, google_cx)

    return providers
