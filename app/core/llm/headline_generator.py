"""Генерация вариантов заголовка (раздел 10.4 SPEC.md, шаг 3)."""
from __future__ import annotations

from app.core.llm.client import LLMClient


def generate_headlines(client: LLMClient, *, text: str, count: int = 3) -> list[str]:
    system_prompt = client.load_prompt("system")
    template = client.load_prompt("headline")
    user_prompt = client.render(template, TEXT=text)

    raw_response = client.generate(system_prompt, user_prompt)
    variants = [line.strip(" -•\t") for line in raw_response.splitlines() if line.strip()]
    return variants[:count]
