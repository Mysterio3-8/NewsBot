"""Сборка сервисов из config.yaml + .env. Используется и UI, и headless-режимом."""
from __future__ import annotations

import os

from app.config.loader import AppConfig
from app.core.monitoring.telegram_fetcher import TelegramFetcher
from app.core.monitoring.vk_fetcher import VKFetcher
from app.core.publishing.telegram_publisher import TelegramPublisher
from app.core.publishing.vk_publisher import VKPublisher


def build_telegram_publisher(config: AppConfig) -> TelegramPublisher | None:
    bot_token = os.environ.get(config.publishing.telegram.token_env)
    if not bot_token:
        return None
    return TelegramPublisher(bot_token)


def build_vk_publisher(config: AppConfig) -> VKPublisher | None:
    group_token = os.environ.get(config.publishing.vk.token_env)
    if not group_token:
        return None
    return VKPublisher(group_token)


def build_telegram_fetcher() -> TelegramFetcher | None:
    api_id = os.environ.get("TG_API_ID")
    api_hash = os.environ.get("TG_API_HASH")
    if not api_id or not api_hash:
        return None
    session_name = os.environ.get("TG_SESSION_NAME", "news_rewriter_session")
    return TelegramFetcher(api_id=int(api_id), api_hash=api_hash, session_name=session_name)


def build_vk_fetcher() -> VKFetcher | None:
    user_token = os.environ.get("VK_USER_TOKEN")
    if not user_token:
        return None
    return VKFetcher(user_token)
