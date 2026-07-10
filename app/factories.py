"""Сборка сервисов из config.yaml + .env. Используется и UI, и headless-режимом."""
from __future__ import annotations

import os

from app.config.loader import AppConfig
from app.core.images.providers.base import ImageProvider
from app.db.models import Channel
from app.core.images.providers.google_provider import GoogleImageProvider
from app.core.images.providers.pexels_provider import PexelsProvider
from app.core.images.providers.pixabay_provider import PixabayProvider
from app.core.images.providers.unsplash_provider import UnsplashProvider
from app.core.monitoring.telegram_fetcher import TelegramFetcher
from app.core.monitoring.vk_fetcher import VKFetcher
from app.core.publishing.telegram_publisher import TelegramPublisher
from app.core.publishing.token_bucket import TokenBucket
from app.core.publishing.vk_publisher import VKPublisher


def build_telegram_publisher(config: AppConfig) -> TelegramPublisher | None:
    bot_token = os.environ.get(config.publishing.telegram.token_env)
    if not bot_token:
        return None
    return TelegramPublisher(bot_token)


def build_vk_publisher(
    config: AppConfig, *, token_bucket: TokenBucket | None = None
) -> VKPublisher | None:
    group_token = os.environ.get(config.publishing.vk.token_env)
    if not group_token:
        return None
    # Опционально: личный токен админа группы ТОЛЬКО для загрузки фото/видео —
    # group-токен не может (VK error 27, см. CLAUDE.md). wall.post всегда идёт через
    # group_token. Не задан — best-effort продолжает публиковать текстом без вложения.
    upload_token = os.environ.get("VK_PHOTO_UPLOAD_TOKEN")
    return VKPublisher(group_token, upload_token=upload_token, token_bucket=token_bucket)


def build_telegram_publisher_for_channel(channel: Channel) -> TelegramPublisher | None:
    """Publisher канала: токен бота берётся из env-переменной, ИМЯ которой хранится в
    channel.tg_token_env (сам токен — в .env, инвариант). Разные каналы могут постить
    одним ботом (одно имя env) в разные @назначения или разными ботами."""
    bot_token = os.environ.get(channel.tg_token_env)
    if not bot_token:
        return None
    return TelegramPublisher(bot_token)


def build_vk_publisher_for_channel(
    channel: Channel, *, token_bucket: TokenBucket | None = None
) -> VKPublisher | None:
    """Publisher канала: групповой токен из channel.vk_token_env + опц. личный upload-
    токен из channel.vk_upload_token_env (group-токен не грузит медиа, VK error 27)."""
    group_token = os.environ.get(channel.vk_token_env)
    if not group_token:
        return None
    upload_token = os.environ.get(channel.vk_upload_token_env)
    return VKPublisher(group_token, upload_token=upload_token, token_bucket=token_bucket)


def build_telegram_fetcher() -> TelegramFetcher | None:
    api_id = os.environ.get("TG_API_ID")
    api_hash = os.environ.get("TG_API_HASH")
    if not api_id or not api_hash:
        return None
    session_name = os.environ.get("TG_SESSION_NAME", "news_rewriter_session")
    return TelegramFetcher(api_id=int(api_id), api_hash=api_hash, session_name=session_name)


def build_vk_fetcher(*, token_bucket: TokenBucket | None = None) -> VKFetcher | None:
    user_token = os.environ.get("VK_USER_TOKEN")
    if not user_token:
        return None
    return VKFetcher(user_token, token_bucket=token_bucket)


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
