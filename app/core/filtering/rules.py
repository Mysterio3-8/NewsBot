"""Локальные эвристики фильтрации до вызова LLM (раздел 8 SPEC.md)."""
from __future__ import annotations

STRUCTURAL_NON_NEWS_TYPES = {"poll", "pinned", "service", "ad"}


def is_structural_non_news(post_type: str) -> str | None:
    """Проверка по типу поста (опрос/закреп/служебное сообщение), не по тексту."""
    if post_type in STRUCTURAL_NON_NEWS_TYPES:
        return f"структурно не новость: {post_type}"
    return None


def find_blacklisted_word(text: str, blacklist_keywords: list[str]) -> str | None:
    lowered_text = text.lower()
    for word in blacklist_keywords:
        if word.lower() in lowered_text:
            return word
    return None


def find_whitelisted_keywords(text: str, whitelist_keywords: list[str]) -> list[str]:
    lowered_text = text.lower()
    return [word for word in whitelist_keywords if word.lower() in lowered_text]
