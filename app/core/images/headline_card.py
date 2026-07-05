"""Карточка "заголовок на фото" — дуотон-тонировка + крупный жирный заголовок
белым внизу (запрос пользователя 2026-07-05, референс в стиле "P|"-карточек).
Отдельная опция от логотипа-вотермарка (см. app/core/images/watermark.py) —
включается через headline_card.enabled в config.yaml."""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont, ImageOps

from app.config.loader import HeadlineCardConfig
from app.paths import PROJECT_ROOT


class HeadlineCardError(Exception):
    """Шрифт не найден — не тихий fallback."""


def apply_duotone(
    image: Image.Image, dark: tuple[int, int, int], light: tuple[int, int, int]
) -> Image.Image:
    """Заменяет цвет фото двухцветным градиентом (тени → dark, света → light) —
    классический приём фото-карточек новостных пабликов."""
    gray = ImageOps.grayscale(image.convert("RGB"))
    return ImageOps.colorize(gray, black=dark, white=light).convert("RGBA")


def overlay_headline(image: Image.Image, headline: str, config: HeadlineCardConfig) -> Image.Image:
    """Затемняющий градиент внизу (для читаемости) + жирный заголовок белым,
    перенос по словам под ширину фото."""
    font_path = PROJECT_ROOT / config.font_path
    if not font_path.exists():
        raise HeadlineCardError(f"Шрифт не найден: {font_path}")

    width, height = image.size
    band_height = int(height * config.band_height_ratio)
    band = _gradient_band(width, band_height)
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


def _gradient_band(width: int, band_height: int) -> Image.Image:
    """Прозрачно-чёрная полоса снизу вверх (плавно темнеет к низу фото) —
    чтобы белый текст был читаем на любом фоне."""
    gradient = Image.new("L", (1, band_height))
    for y in range(band_height):
        gradient.putpixel((0, y), int(230 * (y / band_height) ** 1.6))
    gradient = gradient.resize((width, band_height))
    band = Image.new("RGBA", (width, band_height), (0, 0, 0, 255))
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
