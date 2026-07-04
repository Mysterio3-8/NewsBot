"""Уникализация изображения: лёгкий кроп + шум + удаление метаданных.

Цель — снизить перцептивное совпадение с оригиналом (обход антидубликат-хэшей
соцсетей при репосте) без заметной потери качества. Изменения намеренно малы:
кроп на доли процента и слабый гауссов шум глазом не видны, но меняют
пиксельный/EXIF-отпечаток. Отключается через config (`images.uniquify`).
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from app.config.loader import UniquifyConfig


def uniquify(image: Image.Image, config: UniquifyConfig) -> Image.Image:
    """Возвращает новое изображение с применёнными кропом и шумом. EXIF в PIL-объекте
    не переносится при пересоздании через numpy — метаданные отпадают автоматически,
    плюс явно не копируем image.info при сохранении (см. Watermarker._save)."""
    if not config.enabled:
        return image

    result = _crop_percent(image, config.crop_percent)
    result = _add_noise(result, config.noise_sigma)
    return result


def _crop_percent(image: Image.Image, crop_percent: float) -> Image.Image:
    """Обрезает по crop_percent% с каждой стороны и растягивает обратно до исходного
    размера — итоговые габариты те же, но контент сдвинут/пересемплирован."""
    if crop_percent <= 0:
        return image

    width, height = image.size
    dx = int(width * crop_percent / 100)
    dy = int(height * crop_percent / 100)
    if dx == 0 and dy == 0:
        return image

    cropped = image.crop((dx, dy, width - dx, height - dy))
    return cropped.resize((width, height), Image.LANCZOS)


def _add_noise(image: Image.Image, sigma: float) -> Image.Image:
    """Добавляет слабый гауссов шум к RGB-каналам (альфа не трогаем)."""
    if sigma <= 0:
        return image

    has_alpha = image.mode == "RGBA"
    rgb = image.convert("RGB")
    array = np.asarray(rgb, dtype=np.int16)
    noise = np.random.normal(0, sigma, array.shape).astype(np.int16)
    noisy = np.clip(array + noise, 0, 255).astype(np.uint8)
    result = Image.fromarray(noisy, mode="RGB")

    if has_alpha:
        result = result.convert("RGBA")
        result.putalpha(image.split()[3])
    return result
