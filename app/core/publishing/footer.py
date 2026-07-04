"""Подпись-футер со ссылками на TG/VK в конце опубликованного поста."""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.config.loader import FooterConfig

_VK_GROUP_ID_PATTERN = re.compile(r"vk\.com/(club|public)(\d+)")


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


def build_vk_footer(links: FooterLinks) -> str:
    """Для VK — именованные ссылки через wiki-разметку [ссылка|Текст].
    Ссылка на саму VK-группу — через внутренний формат [club<id>|Текст]: обычный
    полный URL на СВОЮ же группу VK не всегда разлинковывает надёжно, внутренняя
    ссылка на club/public ID — штатный VK-способ сослаться на сообщество."""
    parts = []
    if links.telegram_url:
        parts.append(f"[{links.telegram_url}|Telegram]")
    if links.vk_url:
        parts.append(f"[{_vk_reference(links.vk_url)}|VK]")
    if not parts:
        return ""
    return f"{links.label}: " + " ".join(parts)


def _vk_reference(vk_url: str) -> str:
    match = _VK_GROUP_ID_PATTERN.search(vk_url)
    if match:
        return f"{match.group(1)}{match.group(2)}"
    return vk_url
