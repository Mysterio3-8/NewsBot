"""Watermark на изображениях (раздел 12.3 SPEC.md)."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.config.loader import WatermarkConfig
from app.paths import OUTPUT_DIR, PROJECT_ROOT

ASPECT_RATIOS = {
    "1:1": (1, 1),
    "4:5": (4, 5),
    "16:9": (16, 9),
    "9:16": (9, 16),
}
LOGO_WIDTH_RATIO = 0.2  # логотип занимает 20% ширины итогового изображения


class WatermarkError(Exception):
    """Логотип не найден или конфигурация некорректна — не тихий fallback."""


class Watermarker:
    def __init__(self, config: WatermarkConfig) -> None:
        self._config = config

    def apply(
        self,
        image_path: Path,
        *,
        target_aspect_ratio: str,
        post_id: int,
        channel_name: str | None = None,
    ) -> Path:
        image = Image.open(image_path).convert("RGBA")
        image = crop_to_aspect_ratio(image, target_aspect_ratio)
        image = self._overlay_logo(image)

        if self._config.channel_name_text and channel_name:
            image = self._overlay_text(image, channel_name)

        return self._save(image, image_path, post_id)

    def _overlay_logo(self, image: Image.Image) -> Image.Image:
        logo_path = PROJECT_ROOT / self._config.logo_path
        if not logo_path.exists():
            raise WatermarkError(f"Логотип не найден: {logo_path}")

        logo = Image.open(logo_path).convert("RGBA")
        logo = _resize_logo(logo, image.width)
        logo = _apply_opacity(logo, self._config.opacity)

        position = _compute_position(image.size, logo.size, self._config.position, self._config.margin_px)
        image.alpha_composite(logo, dest=position)
        return image

    def _overlay_text(self, image: Image.Image, channel_name: str) -> Image.Image:
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()
        margin = self._config.margin_px
        draw.text((margin, margin), channel_name, font=font, fill=(255, 255, 255, 220))
        return image

    def _save(self, image: Image.Image, original_path: Path, post_id: int) -> Path:
        output_dir = OUTPUT_DIR / "images" / str(post_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / original_path.name
        image.convert("RGB").save(output_path)
        return output_path


def crop_to_aspect_ratio(image: Image.Image, ratio_key: str) -> Image.Image:
    if ratio_key not in ASPECT_RATIOS:
        raise WatermarkError(f"Неизвестное соотношение сторон: {ratio_key}")

    ratio_w, ratio_h = ASPECT_RATIOS[ratio_key]
    target_ratio = ratio_w / ratio_h
    current_ratio = image.width / image.height

    if current_ratio > target_ratio:
        new_width = int(image.height * target_ratio)
        left = (image.width - new_width) // 2
        return image.crop((left, 0, left + new_width, image.height))

    new_height = int(image.width / target_ratio)
    top = (image.height - new_height) // 2
    return image.crop((0, top, image.width, top + new_height))


def _resize_logo(logo: Image.Image, target_image_width: int) -> Image.Image:
    logo_width = int(target_image_width * LOGO_WIDTH_RATIO)
    logo_height = int(logo.height * (logo_width / logo.width))
    return logo.resize((logo_width, logo_height))


def _apply_opacity(logo: Image.Image, opacity_percent: int) -> Image.Image:
    alpha = logo.split()[3]
    alpha = alpha.point(lambda a: int(a * opacity_percent / 100))
    logo.putalpha(alpha)
    return logo


def _compute_position(
    image_size: tuple[int, int], logo_size: tuple[int, int], position: str, margin: int
) -> tuple[int, int]:
    image_w, image_h = image_size
    logo_w, logo_h = logo_size

    x = margin if "left" in position else image_w - logo_w - margin
    y = margin if "top" in position else image_h - logo_h - margin
    return (x, y)
