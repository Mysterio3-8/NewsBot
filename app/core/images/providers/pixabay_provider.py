"""Легальный сток Pixabay (раздел 12.1 SPEC.md, официальный API)."""
from __future__ import annotations

import requests

from app.core.images.providers.base import ImageResult

API_URL = "https://pixabay.com/api/"
MIN_PER_PAGE = 3  # ограничение самого Pixabay API


class PixabayProvider:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def search(self, query: str, count: int) -> list[ImageResult]:
        response = requests.get(
            API_URL,
            params={"key": self._api_key, "q": query, "per_page": max(MIN_PER_PAGE, count)},
            timeout=15,
        )
        response.raise_for_status()
        hits = response.json().get("hits", [])
        return [
            ImageResult(source_provider="pixabay", url=hit["largeImageURL"])
            for hit in hits[:count]
        ]
