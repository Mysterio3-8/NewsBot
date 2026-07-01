"""Поисковый запрос для картинки, если своих фото нет (раздел 10.4 SPEC.md, шаг 4)."""
from __future__ import annotations

from app.core.llm.client import LLMClient


def generate_image_query(client: LLMClient, *, text: str) -> str:
    system_prompt = client.load_prompt("system")
    template = client.load_prompt("image_query")
    user_prompt = client.render(template, TEXT=text)
    return client.generate(system_prompt, user_prompt).strip()
