"""Полный рерайт текста поста (раздел 10.4 SPEC.md, шаг 2)."""
from __future__ import annotations

from app.core.llm.client import LLMClient


def rewrite_post(client: LLMClient, *, text: str, source: str, style: str) -> str:
    system_prompt = client.load_prompt("system")
    template = client.load_prompt("rewrite")
    style_modifier = _load_style_modifier(client, style)

    user_prompt = client.render(template, TEXT=text, SOURCE=source, STYLE=style_modifier)
    return client.generate(system_prompt, user_prompt).strip()


def _load_style_modifier(client: LLMClient, style: str) -> str:
    try:
        return client.load_prompt(f"style_{style}")
    except FileNotFoundError:
        return style
