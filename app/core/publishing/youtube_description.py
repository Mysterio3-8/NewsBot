"""Описание для загрузки на YouTube: текст фильма/клипа + призыв идти в наш VK и TG.

YouTube — витрина, трафик гоним в свои VK-группу и Telegram-канал (ТЗ 2026-07-22).
Ссылки в описании кликабельны автоматически, спец-разметки не нужно.
"""
from __future__ import annotations

YOUTUBE_TITLE_LIMIT = 100
YOUTUBE_DESCRIPTION_LIMIT = 5000


def build_vk_group_url(vk_destination: str | None) -> str | None:
    """Числовой destination (240120678) → публичный URL группы. Уже готовый URL или
    пусто — возвращаем как есть."""
    if not vk_destination:
        return None
    if vk_destination.startswith("http"):
        return vk_destination
    if vk_destination.lstrip("-").isdigit():
        return f"https://vk.com/public{vk_destination.lstrip('-')}"
    return f"https://vk.com/{vk_destination}"


def build_youtube_title(title: str, *, is_short: bool) -> str:
    """Заголовок ≤100 символов. У Shorts дописываем #Shorts — так YouTube надёжнее
    относит вертикальный ролик к Shorts-ленте."""
    suffix = " #Shorts" if is_short else ""
    limit = YOUTUBE_TITLE_LIMIT - len(suffix)
    clean = " ".join(title.split())
    if len(clean) > limit:
        clean = clean[: limit - 1].rstrip() + "…"
    return clean + suffix


def build_youtube_description(
    body: str, *, vk_url: str | None, tg_url: str | None
) -> str:
    """Текст + блок ссылок на VK и TG. Пустые ссылки пропускаются."""
    parts = [body.strip()] if body.strip() else []
    links = []
    if tg_url:
        links.append(f"📱 Telegram: {tg_url}")
    if vk_url:
        links.append(f"🔵 ВКонтакте: {vk_url}")
    if links:
        parts.append("Больше фильмов и клипов:\n" + "\n".join(links))
    description = "\n\n".join(parts)
    return description[:YOUTUBE_DESCRIPTION_LIMIT]
