"""Монтаж фото в стиле новостных пабликов (референс PostNews, запрос пользователя
2026-07-05): зелёное свечение в двух углах (нижний-левый + верхний-правый) на КАЖДОМ
фото + крупный жирный заголовок белым внизу ТОЛЬКО на первом фото. Логотип-вотермарк
накладывается отдельно (см. app/core/images/watermark.py). Включается через
headline_card.enabled в config.yaml."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.config.loader import HeadlineCardConfig
from app.paths import PROJECT_ROOT

# Нормированные координаты углов (x, y) в диапазоне 0..1.
_CORNER_POSITIONS = {
    "top-left": (0.0, 0.0),
    "top-right": (1.0, 0.0),
    "bottom-left": (0.0, 1.0),
    "bottom-right": (1.0, 1.0),
}
# Плавность спада свечения от угла к центру: >1 — резче у угла, мягче к центру.
_FADE_FALLOFF = 1.8


class HeadlineCardError(Exception):
    """Шрифт не найден или неизвестный угол — не тихий fallback."""


def apply_corner_fade(image: Image.Image, config: HeadlineCardConfig) -> Image.Image:
    """Полупрозрачное цветное свечение из указанных углов, плавно затухающее к
    центру (как синие углы у PostNews, только цвет из config). Накладывается на
    все фото поста — общий фирменный вид."""
    width, height = image.size
    base = np.asarray(image.convert("RGBA"), dtype=np.float32)

    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    xn = xs / max(width - 1, 1)
    yn = ys / max(height - 1, 1)

    radius = max(config.corner_fade_radius_ratio, 1e-3)
    alpha = np.zeros((height, width), dtype=np.float32)
    for corner in config.corner_fade_corners:
        if corner not in _CORNER_POSITIONS:
            raise HeadlineCardError(f"Неизвестный угол свечения: {corner}")
        cx, cy = _CORNER_POSITIONS[corner]
        dist = np.sqrt((xn - cx) ** 2 + (yn - cy) ** 2)
        contrib = np.clip(1.0 - dist / radius, 0.0, 1.0) ** _FADE_FALLOFF
        alpha = np.maximum(alpha, contrib)
    alpha *= config.corner_fade_max_alpha

    color = np.array(config.corner_fade_color, dtype=np.float32)
    rgb = base[..., :3]
    blended = rgb * (1.0 - alpha[..., None]) + color * alpha[..., None]
    out = base.copy()
    out[..., :3] = blended
    return Image.fromarray(out.astype(np.uint8), "RGBA")


def overlay_headline(image: Image.Image, headline: str, config: HeadlineCardConfig) -> Image.Image:
    """Затемняющая цветная полоса внизу (для читаемости белого текста на любом фоне)
    + жирный заголовок белым, перенос по словам под ширину фото. Только на первом фото."""
    font_path = PROJECT_ROOT / config.font_path
    if not font_path.exists():
        raise HeadlineCardError(f"Шрифт не найден: {font_path}")

    width, height = image.size
    band_height = int(height * config.band_height_ratio)
    band = _gradient_band(width, band_height, tuple(config.band_color))
    image.alpha_composite(band, dest=(0, height - band_height))

    font_size = max(12, int(height * config.font_size_ratio))
    font = ImageFont.truetype(str(font_path), font_size)
    max_text_width = width - 2 * config.margin_px
    lines = _wrap_text(headline.upper(), font, max_text_width)

    draw = ImageDraw.Draw(image)
    line_height = int(font_size * 1.2)
    text_block_height = line_height * len(lines)
    y = height - config.margin_px - text_block_height
    fill = (*config.text_color, 255)
    for line in lines:
        draw.text((config.margin_px, y), line, font=font, fill=fill)
        y += line_height

    return image


def _gradient_band(width: int, band_height: int, color: tuple[int, int, int]) -> Image.Image:
    """Полоса заданного цвета снизу вверх (плавно исчезает к верху полосы) — чтобы
    белый текст был читаем на любом фоне."""
    gradient = Image.new("L", (1, band_height))
    for y in range(band_height):
        gradient.putpixel((0, y), int(235 * (y / band_height) ** 1.6))
    gradient = gradient.resize((width, band_height))
    band = Image.new("RGBA", (width, band_height), (*color, 255))
    band.putalpha(gradient)
    return band


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines
