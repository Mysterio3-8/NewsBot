"""Детекция и удаление чужой промо-плашки на фото по ЦВЕТУ (без vision-LLM).

Источники вроде «Кинопремьеры» лепят на кадры яркую жёлтую плашку с текстом
"СЕРИАЛ ИЩИ В КОММЕНТАРИЯХ" — чужое оформление, публиковать нельзя. Vision-детектор
(Groq) на это ненадёжен (лимит 429). Плашка — плотный ярко-жёлтый прямоугольник,
детектится по цвету детерминированно.

Кино-фото часто = вертикальный коллаж из 2 кадров, плашка на одном из них. Запрос
пользователя 2026-07-14: лучше ОБРЕЗАТЬ до одного чистого кадра, а не выбрасывать всё
фото; если плашку так не изолировать (по центру/через оба кадра) — фото не брать.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

_MIN_YELLOW_RATIO = 0.004
_MIN_FILL_RATIO = 0.45


def _banner_bbox(pixels: np.ndarray) -> tuple[int, int, int, int] | None:
    """(x0, y0, x1, y1) плотной ярко-жёлтой плашки или None, если её нет."""
    red, green, blue = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
    yellow = (red > 150) & (green > 150) & (blue < 120) & ((red + green) // 2 - blue > 80)

    total = pixels.shape[0] * pixels.shape[1]
    yellow_count = int(yellow.sum())
    if yellow_count < total * _MIN_YELLOW_RATIO:
        return None

    ys, xs = np.where(yellow)
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    bbox_area = (x1 - x0 + 1) * (y1 - y0 + 1)
    if yellow_count < bbox_area * _MIN_FILL_RATIO:
        return None
    return x0, y0, x1, y1


def has_promo_banner(image_path: Path | str) -> bool:
    """True — на фото есть плотная ярко-жёлтая промо-плашка (чужое оформление)."""
    try:
        image = Image.open(image_path).convert("RGB")
    except Exception:
        return False
    return _banner_bbox(np.asarray(image).astype(np.int16)) is not None


def crop_to_clean_frame(image_path: Path | str) -> str | None:
    """Обрезать вертикальный 2-кадровый коллаж до ЧИСТОГО кадра (той половины, где нет
    плашки). Возвращает путь к обрезанному файлу. None — если плашки нет, ИЛИ она через
    середину/оба кадра (кропом не изолировать → фото не брать).

    Проверка после кропа: если в оставшейся половине плашка всё ещё есть — None."""
    try:
        image = Image.open(image_path).convert("RGB")
    except Exception:
        return None

    bbox = _banner_bbox(np.asarray(image).astype(np.int16))
    if bbox is None:
        return None

    _, y0, _, y1 = bbox
    mid = image.height / 2
    if y0 >= mid:  # плашка целиком в нижнем кадре → оставляем верхний
        cropped = image.crop((0, 0, image.width, int(mid)))
    elif y1 <= mid:  # плашка целиком в верхнем кадре → оставляем нижний
        cropped = image.crop((0, int(mid), image.width, image.height))
    else:  # через середину — одним кадром не изолировать
        return None

    if _banner_bbox(np.asarray(cropped).astype(np.int16)) is not None:
        return None  # плашка залезает и в оставшуюся половину — не спасти кропом

    path = Path(image_path)
    out = path.with_name(f"{path.stem}_frame{path.suffix}")
    cropped.save(out)
    return str(out)
