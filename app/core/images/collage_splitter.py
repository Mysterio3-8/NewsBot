"""Расклейка склеенного коллажа на отдельные кадры (кино-канал).

Источник «Кинопремьеры» часто склеивает 2 кадра фильма вертикально в одно фото.
Запрос пользователя 2026-07-14: расклеить — получить отдельные кадры (из 2 коллажей
по 2 кадра выйдет 4 одиночных фото), чтобы публиковать их по одному.

Режем ТОЛЬКО когда фото действительно склейка: вытянутое по вертикали + есть чёткий
горизонтальный шов (граница кадров) в средней трети. Одиночное фото (пейзаж/портрет
без шва) не трогаем — иначе разрежем цельный кадр пополам.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

_MIN_ASPECT = 1.1        # height/width — ниже считаем одиночным кадром (не режем)
_SEAM_DIFF_THRESHOLD = 22  # сила разрыва между кадрами (средняя |Δ| соседних строк)
_MIN_PART_RATIO = 0.25   # каждая часть после реза — не меньше этой доли высоты


def split_vertical_collage(image_path: Path | str) -> list[str]:
    """Список путей к кадрам. Если фото не склейка — [исходный путь] без изменений."""
    path = Path(image_path)
    try:
        image = Image.open(path).convert("RGB")
    except Exception:
        return [str(path)]

    width, height = image.size
    if height < width * _MIN_ASPECT:
        return [str(path)]  # не вытянутое → одиночный кадр

    pixels = np.asarray(image).astype(np.int16)
    band = range(int(height * 0.38), int(height * 0.62))
    seam_y, seam_strength = max(
        ((y, float(np.abs(pixels[y] - pixels[y - 1]).mean())) for y in band),
        key=lambda t: t[1],
    )
    if seam_strength < _SEAM_DIFF_THRESHOLD:
        return [str(path)]  # чёткого шва нет → цельное фото, не режем

    if min(seam_y, height - seam_y) < height * _MIN_PART_RATIO:
        return [str(path)]  # шов у самого края — не похоже на 50/50 склейку

    top = image.crop((0, 0, width, seam_y))
    bottom = image.crop((0, seam_y, width, height))
    top_path = path.with_name(f"{path.stem}_p1{path.suffix}")
    bottom_path = path.with_name(f"{path.stem}_p2{path.suffix}")
    top.save(top_path)
    bottom.save(bottom_path)
    return [str(top_path), str(bottom_path)]
