"""Watermark на изображениях (раздел 12.3 SPEC.md)."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.config.loader import HeadlineCardConfig, UniquifyConfig, WatermarkConfig
from app.core.images.headline_card import apply_duotone, overlay_headline
from app.core.images.uniquifier import uniquify
from app.paths import OUTPUT_DIR, PROJECT_ROOT

ASPECT_RATIOS = {
    "1:1": (1, 1),
    "4:5": (4, 5),
    "16:9": (16, 9),
    "9:16": (9, 16),
}

# Не резать больше 2% исходной ширины/высоты ради точного целевого соотношения —
# иначе landscape-фото (типичный источник TG/VK) при "4:5" теряло ~40% кадра,
# вырезая важную часть сюжета (жалоба пользователя 2026-07-05, ужесточено до 1-2%
# по повторному запросу пользователя в тот же день). Если точного соотношения не
# достичь в рамках этого лимита — отдаём максимально близкое, но не режем сильнее.
MAX_CROP_FRACTION = 0.02


class WatermarkError(Exception):
    """Логотип не найден или конфигурация некорректна — не тихий fallback."""


class Watermarker:
    def __init__(
        self,
        config: WatermarkConfig,
        uniquify_config: UniquifyConfig | None = None,
        headline_card_config: HeadlineCardConfig | None = None,
    ) -> None:
        self._config = config
        self._uniquify_config = uniquify_config or UniquifyConfig()
        self._headline_card_config = headline_card_config or HeadlineCardConfig()

    def apply(
        self,
        image_path: Path,
        *,
        target_aspect_ratio: str,
        post_id: int,
        headline: str | None = None,
    ) -> Path:
        image = Image.open(image_path).convert("RGBA")
        image = crop_to_aspect_ratio(image, target_aspect_ratio)
        image = uniquify(image, self._uniquify_config)
        if self._headline_card_config.enabled and headline:
            image = apply_duotone(
                image,
                tuple(self._headline_card_config.duotone_dark),
                tuple(self._headline_card_config.duotone_light),
            )
            image = overlay_headline(image, headline, self._headline_card_config)
        image = self._overlay_logo(image)
        return self._save(image, image_path, post_id)

    def _overlay_logo(self, image: Image.Image) -> Image.Image:
        logo_path = PROJECT_ROOT / self._config.logo_path
        if not logo_path.exists():
            raise WatermarkError(f"Логотип не найден: {logo_path}")

        logo = Image.open(logo_path).convert("RGBA")
        logo = _resize_logo(logo, image.width, self._config.size_ratio)
        logo = _apply_opacity(logo, self._config.opacity)

        position = _compute_position(image.size, logo.size, self._config.position, self._config.margin_px)
        image.alpha_composite(logo, dest=position)
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
        ideal_width = int(image.height * target_ratio)
        min_width = int(image.width * (1 - MAX_CROP_FRACTION))
        new_width = max(ideal_width, min_width)
        left = (image.width - new_width) // 2
        return image.crop((left, 0, left + new_width, image.height))

    ideal_height = int(image.width / target_ratio)
    min_height = int(image.height * (1 - MAX_CROP_FRACTION))
    new_height = max(ideal_height, min_height)
    top = (image.height - new_height) // 2
    return image.crop((0, top, image.width, top + new_height))


def _resize_logo(logo: Image.Image, target_image_width: int, size_ratio: float) -> Image.Image:
    logo_width = int(target_image_width * size_ratio)
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
