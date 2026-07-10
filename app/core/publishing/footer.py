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


def build_footer_links_from_config(footer_config: FooterConfig) -> FooterLinks | None:
    if not footer_config.enabled:
        return None
    return FooterLinks(
        telegram_url=footer_config.telegram_url or None,
        telegram_signature=footer_config.telegram_signature,
        subscribe_cta=footer_config.subscribe_cta,
    )


def build_html_footer(links: FooterLinks) -> str:
    """Telegram — фирменная подпись гиперссылкой на канал (HTML parse_mode)."""
    if not links.telegram_url:
        return ""
    return f'<a href="{links.telegram_url}">{links.telegram_signature}</a>'


def build_vk_footer(links: FooterLinks) -> str:
    """VK / Instagram — призыв подписаться на Telegram + голый (кликабельный) URL."""
    if not links.telegram_url:
        return ""
    return f"{links.subscribe_cta}\n{links.telegram_url}"
