"""Классификация поста через LLM (раздел 10.5 SPEC.md — строгий JSON)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.core.llm.client import LLMClient

JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
MAX_PARSE_ATTEMPTS = 2


class ClassificationError(Exception):
    """LLM не вернула валидный JSON после повторной попытки (status: error_classification)."""


@dataclass(frozen=True)
class ClassificationResult:
    is_news: bool
    category: str
    score: int
    reasons: list[str]
    reject_reason: str | None


def classify_post(
    client: LLMClient, *, text: str, source: str, keywords: list[str]
) -> ClassificationResult:
    system_prompt = client.load_prompt("system")
    template = client.load_prompt("classifier")
    user_prompt = client.render(template, TEXT=text, SOURCE=source, KEYWORDS=", ".join(keywords))

    for _ in range(MAX_PARSE_ATTEMPTS):
        raw_response = client.generate(system_prompt, user_prompt)
        result = _try_parse(raw_response)
        if result is not None:
            return result

    raise ClassificationError("LLM вернула невалидный JSON классификации дважды подряд")


def _try_parse(raw_response: str) -> ClassificationResult | None:
    match = JSON_OBJECT_PATTERN.search(raw_response)
    if not match:
        return None

    try:
        data = json.loads(match.group(0))
        return ClassificationResult(
            is_news=bool(data["is_news"]),
            category=str(data["category"]),
            score=int(data["score"]),
            reasons=[str(r) for r in data.get("reasons", [])],
            reject_reason=data.get("reject_reason"),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
