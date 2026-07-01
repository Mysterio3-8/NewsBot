"""Фото из самого поста — наивысший приоритет (раздел 12.1 SPEC.md)."""
from __future__ import annotations

from pathlib import Path

from app.core.images.providers.base import ImageResult


class SourceImageProvider:
    """Оборачивает уже скачанные из поста файлы в единый интерфейс ImageProvider."""

    def __init__(self, post_image_paths: list[Path]) -> None:
        self._image_paths = post_image_paths

    def search(self, query: str, count: int) -> list[ImageResult]:
        return [
            ImageResult(source_provider="source", local_path=path)
            for path in self._image_paths[:count]
        ]
