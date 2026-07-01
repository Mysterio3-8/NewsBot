"""Подпись-футер со ссылками на TG/VK в конце опубликованного поста."""
from __future__ import annotations

from dataclasses import dataclass

from app.config.loader import FooterConfig


@dataclass(frozen=True)
class FooterLinks:
    label: str
    telegram_url: str | None = None
    vk_url: str | None = None


def build_footer_links_from_config(footer_config: FooterConfig) -> FooterLinks | None:
    if not footer_config.enabled:
        return None
    return FooterLinks(
        label=footer_config.label,
        telegram_url=footer_config.telegram_url or None,
        vk_url=footer_config.vk_url or None,
    )


def build_html_footer(links: FooterLinks) -> str:
    """Для Telegram — именованные гиперссылки (HTML parse_mode)."""
    parts = []
    if links.telegram_url:
        parts.append(f'<a href="{links.telegram_url}">Telegram</a>')
    if links.vk_url:
        parts.append(f'<a href="{links.vk_url}">VK</a>')
    if not parts:
        return ""
    return f"{links.label}: " + " | ".join(parts)


def build_plain_footer(links: FooterLinks) -> str:
    """Для VK — обычный пост на стене не поддерживает именованные ссылки, только голый URL."""
    urls = [url for url in (links.telegram_url, links.vk_url) if url]
    if not urls:
        return ""
    return f"{links.label}: " + " ".join(urls)
