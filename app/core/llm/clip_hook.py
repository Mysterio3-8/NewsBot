"""AI-хуки для вертикальных клипов: короткая цепляющая надпись поверх каждого клипа.

Сбой LLM — fail-open на название фильма: клип с названием лучше, чем клип без надписи
и чем отсутствие клипа (ТЗ 2026-07-21).
"""
from __future__ import annotations

import json
import logging
import re

from app.core.llm.client import LLMClient
from app.core.llm.sanitizer import strip_foreign_script_artifacts

logger = logging.getLogger("llm")

JSON_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
MAX_HOOK_LENGTH = 40


def parse_hooks(raw_response: str) -> list[str]:
    """JSON {"hooks": [...]} → список; при кривом JSON — построчный разбор ответа."""
    cleaned = JSON_FENCE_PATTERN.sub("", raw_response.strip())
    try:
        hooks = json.loads(cleaned)["hooks"]
        lines = [str(hook).strip() for hook in hooks]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        logger.warning("Хуки клипов: JSON не разобран, fallback на построчный: %s", error)
        lines = [line.strip(" -•\t\"") for line in raw_response.splitlines()]
    return [line for line in lines if line and len(line) <= MAX_HOOK_LENGTH]


def generate_clip_hooks(
    client: LLMClient, *, title: str, description: str, count: int
) -> list[str]:
    """Ровно `count` хуков: недостающие добиваются названием фильма, лишние отбрасываются."""
    hooks: list[str] = []
    try:
        system_prompt = client.load_prompt("system")
        template = client.load_prompt("clip_hook")
        user_prompt = client.render(
            template, TITLE=title, DESCRIPTION=description or title, COUNT=str(count)
        )
        hooks = [
            strip_foreign_script_artifacts(hook)
            for hook in parse_hooks(client.generate(system_prompt, user_prompt))
        ]
    except Exception as error:
        logger.warning("Хуки клипов не сгенерированы, беру название фильма: %s", error)

    hooks = hooks[:count]
    hooks += [title] * (count - len(hooks))
    return hooks
