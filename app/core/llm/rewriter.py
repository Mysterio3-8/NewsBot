"""Полный рерайт текста поста (раздел 10.4 SPEC.md, шаг 2)."""
from __future__ import annotations

from app.core.llm.client import LLMClient
from app.core.llm.sanitizer import strip_foreign_script_artifacts

HASHTAGS_INSTRUCTION_ON = (
    "Хэштеги: в самом конце, отдельной строкой, добавь ровно 3 хэштега по теме новости "
    "на русском языке, слитно, без пробелов внутри слова (например #технологии #apple "
    "#санкции). Хэштеги должны отражать конкретную тему поста (компания/отрасль/событие), "
    "а не быть общими словами вроде #новости. На последней строке — ТОЛЬКО хэштеги, ничего "
    "больше."
)
HASHTAGS_INSTRUCTION_OFF = "Хэштеги добавлять не нужно — не пиши в тексте ни одного #хэштега."


def rewrite_post(
    client: LLMClient,
    *,
    text: str,
    source: str,
    style: str,
    max_length: int,
    include_hashtags: bool = False,
    prompt_name: str = "rewrite",
) -> str:
    system_prompt = client.load_prompt("system")
    # prompt_name — свой промпт рерайта у канала (напр. "rewrite_kino" — красивый
    # подробный кино-стиль вместо терсе-новостного). Дефолт — новостной "rewrite".
    template = client.load_prompt(prompt_name)
    style_modifier = _load_style_modifier(client, style)
    hashtags_instruction = HASHTAGS_INSTRUCTION_ON if include_hashtags else HASHTAGS_INSTRUCTION_OFF

    user_prompt = client.render(
        template,
        TEXT=text,
        SOURCE=source,
        STYLE=style_modifier,
        MAX_LENGTH=str(max_length),
        HASHTAGS_INSTRUCTION=hashtags_instruction,
    )
    result = client.generate(system_prompt, user_prompt).strip()
    return strip_foreign_script_artifacts(result)


def _load_style_modifier(client: LLMClient, style: str) -> str:
    try:
        return client.load_prompt(f"style_{style}")
    except FileNotFoundError:
        return style
