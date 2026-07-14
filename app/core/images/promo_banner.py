"""Детекция чужой промо-плашки на фото по ЦВЕТУ (без vision-LLM).

Источники вроде «Кинопремьеры» лепят на кадры яркую жёлтую плашку с текстом
"ИЩИ В КОММЕНТАРИЯХ" / "СЕРИАЛ ..." — чужое оформление, которое нельзя публиковать.
Vision-детектор (Groq) на это ненадёжен (лимит 429 → fail-closed отбрасывает и чистые
фото зря). Плашка — плотный ярко-жёлтый прямоугольник, поэтому детектится по цвету
детерминированно: считаем ярко-жёлтые пиксели и проверяем, что они образуют плотный
компактный блок (а не разбросанный жёлтый объект в сцене).

Запрос пользователя 2026-07-14: фото с такой плашкой — не брать (блюром её незаметно
не убрать, остаётся жёлтое пятно; чистые кадры того же поста берём как обычно).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

# Доля ярко-жёлтых пикселей от всего кадра, ниже которой плашки точно нет.
_MIN_YELLOW_RATIO = 0.004
# Плотность заливки bbox жёлтым: плашка — сплошной прямоугольник (высокая плотность),
# случайный жёлтый объект в сцене — рыхлый. Порог отсекает ложные срабатывания.
_MIN_FILL_RATIO = 0.45


def has_promo_banner(image_path: Path | str) -> bool:
    """True — на фото есть плотная ярко-жёлтая промо-плашка (чужое оформление)."""
    try:
        image = Image.open(image_path).convert("RGB")
    except Exception:
        return False

    pixels = np.asarray(image).astype(np.int16)
    red, green, blue = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
    yellow = (red > 150) & (green > 150) & (blue < 120) & ((red + green) // 2 - blue > 80)

    total = image.width * image.height
    yellow_count = int(yellow.sum())
    if yellow_count < total * _MIN_YELLOW_RATIO:
        return False

    ys, xs = np.where(yellow)
    bbox_area = (xs.max() - xs.min() + 1) * (ys.max() - ys.min() + 1)
    return bool(yellow_count >= bbox_area * _MIN_FILL_RATIO)
