"""Легальный сток Pexels (раздел 12.1 SPEC.md, официальный API)."""
from __future__ import annotations

import requests

from app.core.images.providers.base import ImageResult

API_URL = "https://api.pexels.com/v1/search"


class PexelsProvider:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def search(self, query: str, count: int) -> list[ImageResult]:
        response = requests.get(
            API_URL,
            params={"query": query, "per_page": count},
            headers={"Authorization": self._api_key},
            timeout=15,
        )
        response.raise_for_status()
        photos = response.json().get("photos", [])
        return [
            ImageResult(source_provider="pexels", url=photo["src"]["large"]) for photo in photos
        ]
