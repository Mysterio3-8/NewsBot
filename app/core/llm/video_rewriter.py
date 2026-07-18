"""AI-рерайт названия и описания видео для ежедневного видео-репоста.

Если у видео нет ни названия, ни описания (нельзя понять, что за фильм) — AI не
используется, видео публикуется как есть (ТЗ 2026-07-18). Любой сбой LLM — fail-open
к оригинальному тексту: репост важнее красоты формулировки.
"""
from __future__ import annotations

import logging

from app.core.llm.client import LLMClient
from app.core.llm.sanitizer import strip_foreign_script_artifacts

logger = logging.getLogger("monitoring")


def rewrite_video_texts(
    client: LLMClient, *, title: str, description: str
) -> tuple[str, str]:
    """(новое название, новое описание). Пустые поля не трогаем; оба пустые → AI
    не вызывается вообще."""
    if not title.strip() and not description.strip():
        return title, description

    new_title = title
    if title.strip():
        new_title = _rewrite_one(client, "video_title", original=title, TITLE=title)
    new_description = description
    if description.strip():
        new_description = _rewrite_one(
            client, "video_description", original=description, DESCRIPTION=description
        )
    return new_title, new_description


def _rewrite_one(client: LLMClient, prompt_name: str, *, original: str, **placeholders) -> str:
    try:
        system_prompt = client.load_prompt("system")
        template = client.load_prompt(prompt_name)
        user_prompt = client.render(template, **placeholders)
        result = strip_foreign_script_artifacts(client.generate(system_prompt, user_prompt).strip())
        return result or original
    except Exception as error:
        logger.warning("AI-рерайт (%s) не удался, оставляю оригинал: %s", prompt_name, error)
        return original
