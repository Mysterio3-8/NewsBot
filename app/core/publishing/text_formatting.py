"""Форматирование рерайта под конкретную площадку: хэштеги отдельно от тела,
разметка **жирного**/*курсива* из промпта в HTML (Telegram) или без неё (VK).
"""
from __future__ import annotations

import html
import re

HASHTAG_LINE_PATTERN = re.compile(r"^(#\S+(?:\s+#\S+)*)\s*$")
BOLD_PATTERN = re.compile(r"\*\*(.+?)\*\*")
ITALIC_PATTERN = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")


def split_hashtags(text: str) -> tuple[str, str]:
    """Отделяет хэштеги, если LLM оставил их последней строкой (как просит промпт)."""
    lines = text.rstrip().splitlines()
    if lines and HASHTAG_LINE_PATTERN.match(lines[-1].strip()):
        hashtags = lines[-1].strip()
        body = "\n".join(lines[:-1]).rstrip()
        return body, hashtags
    return text.rstrip(), ""


def markdown_to_telegram_html(text: str) -> str:
    """**важное** -> <b>важное</b>, *второстепенное* -> <i>второстепенное</i>.
    Экранирование HTML — до подстановки тегов, т.к. * не является спецсимволом HTML.
    """
    escaped = html.escape(text)
    escaped = BOLD_PATTERN.sub(r"<b>\1</b>", escaped)
    escaped = ITALIC_PATTERN.sub(r"<i>\1</i>", escaped)
    return escaped


def strip_markdown(text: str) -> str:
    """VK-посты не поддерживают жирный/курсив — убираем разметку, оставляя чистый текст."""
    text = BOLD_PATTERN.sub(r"\1", text)
    text = ITALIC_PATTERN.sub(r"\1", text)
    return text
