"""Приведение ImageResult к локальному файлу (общий шаг перед watermark)."""
from __future__ import annotations

from pathlib import Path

import requests

from app.core.images.providers.base import ImageResult


def resolve_to_local_file(result: ImageResult, dest_path: Path) -> Path:
    if result.local_path is not None:
        return result.local_path

    if result.image_bytes is not None:
        dest_path.write_bytes(result.image_bytes)
        return dest_path

    if result.url is not None:
        response = requests.get(result.url, timeout=30)
        response.raise_for_status()
        dest_path.write_bytes(response.content)
        return dest_path

    raise ValueError("ImageResult не содержит ни local_path, ни url, ни image_bytes")
