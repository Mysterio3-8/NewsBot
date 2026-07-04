"""Генерация вариантов заголовка (раздел 10.4 SPEC.md, шаг 3)."""
from __future__ import annotations

import json
import logging
import re

from app.core.llm.client import LLMClient
from app.core.llm.sanitizer import strip_foreign_script_artifacts

logger = logging.getLogger("llm")

JSON_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def generate_headlines(client: LLMClient, *, text: str, style: str, count: int = 3) -> list[str]:
    system_prompt = client.load_prompt("system")
    template = client.load_prompt("headline")
    user_prompt = client.render(template, TEXT=text, STYLE=style)

    raw_response = client.generate(system_prompt, user_prompt)
    headlines = _parse_headlines(raw_response)[:count]
    return [strip_foreign_script_artifacts(h) for h in headlines]


def _parse_headlines(raw_response: str) -> list[str]:
    cleaned = JSON_FENCE_PATTERN.sub("", raw_response.strip())
    try:
        headlines = json.loads(cleaned)["headlines"]
        return [h.strip() for h in headlines if h.strip()]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        logger.warning("Не удалось распарсить JSON заголовков, fallback на построчный: %s", error)
        return [line.strip(" -•\t") for line in raw_response.splitlines() if line.strip()]
