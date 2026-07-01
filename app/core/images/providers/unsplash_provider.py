"""Легальный сток Unsplash (раздел 12.1 SPEC.md, официальный API)."""
from __future__ import annotations

import requests

from app.core.images.providers.base import ImageResult

API_URL = "https://api.unsplash.com/search/photos"


class UnsplashProvider:
    def __init__(self, access_key: str) -> None:
        self._access_key = access_key

    def search(self, query: str, count: int) -> list[ImageResult]:
        response = requests.get(
            API_URL,
            params={"query": query, "per_page": count},
            headers={"Authorization": f"Client-ID {self._access_key}"},
            timeout=15,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        return [
            ImageResult(source_provider="unsplash", url=item["urls"]["regular"])
            for item in results
        ]
