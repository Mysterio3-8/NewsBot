"""Подпись-футер в конце опубликованного поста — разная по сетям (ТЗ 2026-07-10):

- Telegram: фирменная подпись именованной гиперссылкой на свой TG-канал.
- VK / Instagram: призыв подписаться на Telegram + голый URL канала.

Голый URL для VK — намеренно: обычная ссылка в тексте VK кликабельна автоматически,
а скобочная wiki-разметка [url|текст] внешние домены (t.me) не разлинковывает — из-за
этого футеры на проде ранее отключили (2026-07-04). Голый URL эту проблему снимает.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config.loader import FooterConfig


@dataclass(frozen=True)
class FooterLinks:
    telegram_url: str | None = None
    telegram_signature: str = "Новости в трёх словах"
    subscribe_cta: str = "Подписывайтесь на Telegram-канал:"
    vk_url: str | None = None
    vk_signature: str = "🔵 Больше контента в нашем VK"


def build_footer_links_from_config(footer_config: FooterConfig) -> FooterLinks | None:
    if not footer_config.enabled:
        return None
    return FooterLinks(
        telegram_url=footer_config.telegram_url or None,
        telegram_signature=footer_config.telegram_signature,
        subscribe_cta=footer_config.subscribe_cta,
    )


def build_channel_footer(
    tg_url: str | None,
    tg_signature: str | None,
    config_footer: FooterConfig,
    fallback: FooterLinks | None,
    vk_url: str | None = None,
) -> FooterLinks | None:
    """Футер конкретного канала: если у канала задан свой tg_footer_url — строим подпись
    с его URL и подписью канала (или брендовой из config, если своя не задана). Иначе —
    глобальный fallback. Убирает дублирование хардкод-подписи по местам публикации."""
    if not tg_url:
        return fallback
    return FooterLinks(
        telegram_url=tg_url,
        telegram_signature=tg_signature or config_footer.telegram_signature,
        subscribe_cta=config_footer.subscribe_cta,
        vk_url=vk_url,
    )


def build_html_footer(links: FooterLinks) -> str:
    """Telegram — фирменные подписи гиперссылками: свой TG-канал и (если задан) свой VK
    отдельной строкой (ТЗ 2026-07-28 — гнать аудиторию из TG ещё и во ВКонтакте)."""
    lines = []
    if links.telegram_url:
        lines.append(f'<a href="{links.telegram_url}">{links.telegram_signature}</a>')
    if links.vk_url:
        lines.append(f'<a href="{links.vk_url}">{links.vk_signature}</a>')
    return "\n".join(lines)


def build_vk_footer(links: FooterLinks) -> str:
    """VK / Instagram — призыв подписаться на Telegram + голый (кликабельный) URL."""
    if not links.telegram_url:
        return ""
    return f"{links.subscribe_cta}\n{links.telegram_url}"
