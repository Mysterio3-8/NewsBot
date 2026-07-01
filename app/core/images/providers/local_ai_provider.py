"""Локальная генерация изображений через HTTP API Stable Diffusion
(AUTOMATIC1111-совместимый /sdapi/v1/txt2img, раздел 12.1 п.3 SPEC.md).

Модель здесь не встроена в проект — сервер поднимается пользователем отдельно,
адрес берётся из config.yaml.
"""
from __future__ import annotations

import base64

import requests

from app.core.images.providers.base import ImageResult

TXT2IMG_ENDPOINT = "/sdapi/v1/txt2img"


class LocalAIImageProvider:
    def __init__(self, host: str) -> None:
        self._host = host.rstrip("/")

    def search(self, query: str, count: int) -> list[ImageResult]:
        response = requests.post(
            f"{self._host}{TXT2IMG_ENDPOINT}",
            json={"prompt": query, "batch_size": count},
            timeout=120,
        )
        response.raise_for_status()
        images_base64 = response.json().get("images", [])
        return [
            ImageResult(source_provider="local_ai", image_bytes=base64.b64decode(image_b64))
            for image_b64 in images_base64
        ]
