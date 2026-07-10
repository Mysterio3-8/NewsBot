"""Поисковый запрос для кадров/постеров фильма (кино-канал, MULTICHANNEL.md срез 2.5).
Отличается от image_query_generator.py: тот просит нейтральную стоковую сцену без имён
собственных, здесь ровно наоборот — нужно ТОЧНОЕ название фильма для Google-поиска."""
from __future__ import annotations

from app.core.llm.client import LLMClient


def generate_movie_search_query(client: LLMClient, *, text: str) -> str:
    system_prompt = client.load_prompt("system")
    template = client.load_prompt("movie_query")
    user_prompt = client.render(template, TEXT=text)
    title = client.generate(system_prompt, user_prompt).strip()
    if not title:
        return ""
    return f"{title} кадр из фильма"
