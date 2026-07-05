"""Подбор изображений по приоритету провайдеров + watermark (разделы 12.1-12.3 SPEC.md)."""
from __future__ import annotations

from pathlib import Path

from app.core.images.providers.base import ImageProvider
from app.core.images.resolver import resolve_to_local_file
from app.core.images.watermark import Watermarker


def prepare_images_for_post(
    *,
    providers_order: list[str],
    providers: dict[str, ImageProvider],
    query: str,
    count: int,
    post_id: int,
    watermarker: Watermarker,
    target_aspect_ratio: str,
    raw_output_dir: Path,
    headline: str | None = None,
) -> list[Path]:
    """Пробует провайдеров по порядку приоритета, останавливаясь на первом с результатами.
    Пустой список — ни один провайдер не нашёл изображений, пост публикуется без фото.
    """
    for provider_name in providers_order:
        provider = providers.get(provider_name)
        if provider is None:
            continue

        results = provider.search(query, count)
        if not results:
            continue

        raw_output_dir.mkdir(parents=True, exist_ok=True)
        return [
            watermarker.apply(
                resolve_to_local_file(result, raw_output_dir / f"raw_{index}.jpg"),
                target_aspect_ratio=target_aspect_ratio,
                post_id=post_id,
                headline=headline,
            )
            for index, result in enumerate(results)
        ]

    return []
