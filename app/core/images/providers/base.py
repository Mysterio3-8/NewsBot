"""Общий интерфейс провайдеров изображений (раздел 12.2 SPEC.md)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ImageResult:
    """Ровно одно из полей url/local_path/image_bytes должно быть заполнено."""

    source_provider: str
    url: str | None = None
    local_path: Path | None = None
    image_bytes: bytes | None = None


class ImageProvider(Protocol):
    def search(self, query: str, count: int) -> list[ImageResult]: ...
