"""Оформление вертикального клипа: логотип канала + заголовок-хук сверху.

Оверлей рисуется одним прозрачным PNG размером с клип (Pillow), а ffmpeg просто
накладывает его поверх кадра. Так сделано вместо ffmpeg-drawtext намеренно: drawtext
требует экранирования кириллицы/эмодзи прямо в строке фильтра, не умеет переносить
текст по словам и не даёт нормальной обводки — а Pillow всё это уже умеет и тем же
шрифтом, что и монтаж фото.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.paths import PROJECT_ROOT

FONT_PATH = PROJECT_ROOT / "assets" / "fonts" / "DejaVuSans-Bold.ttf"
# Жёлтый (а не белый) — на кино-кадрах он темнее теряется реже, чёрная обводка
# держит читаемость на любом фоне.
TEXT_COLOR = (255, 214, 0, 255)
STROKE_COLOR = (0, 0, 0, 255)
STROKE_RATIO = 0.14
FONT_SIZE_RATIO = 0.062
LINE_SPACING_RATIO = 0.22
MARGIN_RATIO = 0.045
LOGO_WIDTH_RATIO = 0.20
MAX_HEADLINE_LINES = 3


class ClipOverlayError(Exception):
    """Шрифт или логотип не найдены — не тихий fallback."""


def wrap_lines(text: str, max_width_px: int, measure) -> list[str]:
    """Перенос по словам: measure(строка) → ширина в пикселях. Слово, не влезающее
    целиком, остаётся на своей строке (обрезать слова посреди — хуже читается)."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and measure(candidate) > max_width_px:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines[:MAX_HEADLINE_LINES]


def render_clip_overlay(
    *,
    headline: str,
    logo_path: Path,
    out_path: Path,
    width: int,
    height: int,
) -> Path:
    """Прозрачный PNG width×height: логотип в правом верхнем углу, хук заглавными
    буквами по центру под ним."""
    if not FONT_PATH.exists():
        raise ClipOverlayError(f"Шрифт не найден: {FONT_PATH}")
    if not logo_path.exists():
        raise ClipOverlayError(f"Логотип не найден: {logo_path}")

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    margin = int(width * MARGIN_RATIO)

    logo = Image.open(logo_path).convert("RGBA")
    logo_width = int(width * LOGO_WIDTH_RATIO)
    logo = logo.resize((logo_width, max(int(logo.height * logo_width / logo.width), 1)))
    canvas.alpha_composite(logo, (width - logo_width - margin, margin))

    text = headline.strip().upper()
    if text:
        _draw_headline(canvas, text, top=margin + logo.height + margin, margin=margin)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "PNG")
    return out_path


def _draw_headline(canvas: Image.Image, text: str, *, top: int, margin: int) -> None:
    width = canvas.width
    font = ImageFont.truetype(str(FONT_PATH), int(width * FONT_SIZE_RATIO))
    draw = ImageDraw.Draw(canvas)
    stroke_width = max(int(font.size * STROKE_RATIO), 2)

    def measure(line: str) -> int:
        return draw.textlength(line, font=font)

    lines = wrap_lines(text, width - 2 * margin, measure)
    line_height = int(font.size * (1 + LINE_SPACING_RATIO))
    for index, line in enumerate(lines):
        draw.text(
            (width // 2, top + index * line_height),
            line,
            font=font,
            fill=TEXT_COLOR,
            stroke_width=stroke_width,
            stroke_fill=STROKE_COLOR,
            anchor="ma",
        )
