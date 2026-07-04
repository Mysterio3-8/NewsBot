"""Фото из самого поста — наивысший приоритет (раздел 12.1 SPEC.md)."""
from __future__ import annotations

from pathlib import Path

from app.core.images.providers.base import ImageResult


class SourceImageProvider:
    """Оборачивает фото, уже присланные источником, в единый ImageProvider.
    VK отдаёт прямые HTTP-URL (resolve_to_local_file их скачивает), а Telegram —
    уже скачанные локальные пути (Telethon сам качает через сессию, URL нет)."""

    def __init__(self, post_images: list[str]) -> None:
        self._images = post_images

    def search(self, query: str, count: int) -> list[ImageResult]:
        return [self._to_result(item) for item in self._images[:count]]

    @staticmethod
    def _to_result(item: str) -> ImageResult:
        if item.startswith("http://") or item.startswith("https://"):
            return ImageResult(source_provider="source", url=item)
        return ImageResult(source_provider="source", local_path=Path(item))
