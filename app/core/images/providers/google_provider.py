"""Google Custom Search (Programmable Search Engine) — картиночный поиск по всему
интернету. В отличие от Pexels/Unsplash/Pixabay (лицензионный сток, абстрактные темы),
используется для поиска РЕАЛЬНЫХ кадров/постеров по конкретному названию (кино-канал,
раздел MULTICHANNEL.md срез 2.5) — Pexels для этого не годится, там нет кадров фильмов.

Бесплатный tier — 100 запросов/день (google_cse_id + google_cse_key в .env). Квота
исчерпывается легко, поэтому search() не бросает исключение наружу при сетевой ошибке —
возвращает [] и пайплайн переходит к «пост без фото» вместо падения всего поста.
"""
from __future__ import annotations

import logging

import requests

from app.core.images.providers.base import ImageResult

API_URL = "https://www.googleapis.com/customsearch/v1"
MAX_PER_REQUEST = 10  # ограничение самого Google Custom Search API

logger = logging.getLogger("monitoring")


class GoogleImageProvider:
    def __init__(self, api_key: str, cx: str) -> None:
        self._api_key = api_key
        self._cx = cx

    def search(self, query: str, count: int) -> list[ImageResult]:
        try:
            response = requests.get(
                API_URL,
                params={
                    "key": self._api_key,
                    "cx": self._cx,
                    "q": query,
                    "searchType": "image",
                    "safe": "active",
                    "num": max(1, min(count, MAX_PER_REQUEST)),
                },
                timeout=15,
            )
            response.raise_for_status()
        except requests.RequestException as error:
            logger.warning("Google Custom Search не удался для запроса %r: %s", query, error)
            return []

        items = response.json().get("items", [])
        return [
            ImageResult(source_provider="google", url=item["link"])
            for item in items[:count]
            if item.get("link")
        ]
