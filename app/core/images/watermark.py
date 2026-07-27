"""Watermark на изображениях (раздел 12.3 SPEC.md)."""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter

from app.config.loader import HeadlineCardConfig, UniquifyConfig, WatermarkConfig
from app.core.images.headline_card import apply_corner_fade, overlay_headline
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

# VK/TG/IG показывают одиночное фото целиком, только если его соотношение сторон в
# «безопасном» окне; за его пределами лента режет превью по центру — и вместе с краями
# уезжают наш заголовок (снизу) и лого (сверху) (жалоба 2026-07-27: «ВК обрезает фото,
# заголовок и лого срезаются, некрасиво»). Держим фото в этом окне: внутри — не трогаем
# вообще (никакого кропа/подложки — «нормальное фото»), шире/уже — вписываем ЦЕЛИКОМ на
# тёмную подложку (сплошную, не блюр — блюр пользователь отверг 2026-07-11). Так ни фото,
# ни наложенные заголовок/лого не обрезаются нигде.
SAFE_MIN_ASPECT = 0.80  # 4:5 — «высоту» до этого VK-лента показывает целиком; выше (портрет)
                        # VK режет по центру → срезает низ с заголовком (жалоба 2026-07-27),
                        # поэтому такие паддим до 4:5.
SAFE_MAX_ASPECT = 1.78  # 16:9 — «ширину» до этого показывает целиком; шире (панорамы) — паддим
SAFE_PAD_COLOR = (12, 20, 16)  # тёмный фон подложки, в тон брендовому зелёному

# Обрезка чужого водяного знака у края (см. watermark_detector.locate_foreign_watermark)
# — ОТДЕЛЬНЫЙ лимит от MAX_CROP_FRACTION выше: тот бережёт композицию от лишней
# обрезки ради формата, а этот убирает чужой брендинг с самого фото (запрос
# пользователя 2026-07-05: "находить оригинальное фото, только без чужого монтажа
# и вотермарков") — здесь важнее полностью убрать знак, чем сохранить каждый пиксель.
WATERMARK_REMOVAL_CROP_FRACTION = 0.20


def crop_out_watermark_regions(
    image: Image.Image, regions: set[str], fraction: float = WATERMARK_REMOVAL_CROP_FRACTION
) -> Image.Image:
    """Обрезает верхнюю и/или нижнюю полосу фото, где обнаружен чужой знак.
    regions — {"top"}, {"bottom"} или {"top", "bottom"}."""
    width, height = image.size
    top = int(height * fraction) if "top" in regions else 0
    bottom = int(height * fraction) if "bottom" in regions else 0
    if top + bottom >= height:
        return image  # защита от вырезания всего фото при абсурдных входных данных
    return image.crop((0, top, width, height - bottom))


class WatermarkError(Exception):
    """Логотип не найден или конфигурация некорректна — не тихий fallback."""


class Watermarker:
    def __init__(
        self,
        config: WatermarkConfig,
        uniquify_config: UniquifyConfig | None = None,
        headline_card_config: HeadlineCardConfig | None = None,
        aspect_mode: str = "blur",
    ) -> None:
        self._config = config
        self._uniquify_config = uniquify_config or UniquifyConfig()
        self._headline_card_config = headline_card_config or HeadlineCardConfig()
        # aspect_mode: "blur" — вписать фото целиком на размытую подложку (не режет,
        # но добавляет размытые поля); "crop" — центр-кроп в соотношение (фото
        # заполняет кадр без полей, но края обрезаются). Запрос пользователя 2026-07-11:
        # NEWS_MAIN — 1:1 без блюра (crop).
        self._aspect_mode = aspect_mode

    def apply(
        self,
        image_path: Path,
        *,
        target_aspect_ratio: str,
        post_id: int,
        headline: str | None = None,
    ) -> Path:
        image = Image.open(image_path).convert("RGBA")
        if self._aspect_mode == "safe":
            image = fit_within_safe_bounds(image)
        elif self._aspect_mode == "crop":
            image = crop_to_aspect_ratio(image, target_aspect_ratio)
        else:
            image = fit_to_aspect_ratio(image, target_aspect_ratio)
        image = uniquify(image, self._uniquify_config)
        if self._headline_card_config.enabled:
            card = self._headline_card_config
            # Цветной фейд по углам — только если он реально настроен (углы + alpha>0).
            # Режим «простое медиа» Новостей передаёт alpha=0 → фейда нет, остаётся
            # только заголовок (запрос 2026-07-27 «убрать фейд вообще, оставить хук»).
            if card.corner_fade_corners and card.corner_fade_max_alpha > 0:
                image = apply_corner_fade(image, card)
            if headline:
                image = overlay_headline(image, headline, card)
        image = self._overlay_logo(image)
        return self._save(image, image_path, post_id)

    def _overlay_logo(self, image: Image.Image) -> Image.Image:
        if not self._config.logo_enabled:
            return image  # режим без вотермарка (простое медиа)
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


def fit_within_safe_bounds(
    image: Image.Image,
    *,
    min_aspect: float = SAFE_MIN_ASPECT,
    max_aspect: float = SAFE_MAX_ASPECT,
    pad_color: tuple[int, int, int] = SAFE_PAD_COLOR,
) -> Image.Image:
    """Фото с соотношением сторон в пределах [min_aspect, max_aspect] — возвращаем как
    есть (лента покажет целиком, ничего не режем и не добавляем). Слишком широкое —
    добавляем поля сверху/снизу до max_aspect; слишком высокое — поля по бокам до
    min_aspect. Контент никогда не режется, подложка сплошная тёмная (не блюр)."""
    width, height = image.size
    aspect = width / height
    if min_aspect <= aspect <= max_aspect:
        return image
    if aspect > max_aspect:
        canvas_w, canvas_h = width, math.ceil(width / max_aspect)
    else:
        canvas_w, canvas_h = math.ceil(height * min_aspect), height
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (*pad_color, 255))
    canvas.paste(image, ((canvas_w - width) // 2, (canvas_h - height) // 2), image)
    return canvas


def fit_to_aspect_ratio(image: Image.Image, ratio_key: str, *, blur_radius: int = 45) -> Image.Image:
    """Приводит фото к целевому соотношению БЕЗ обрезки контента: фото вписывается
    целиком по центру, а недостающие поля заполняются размытой затемнённой копией
    самого фото (приём PostNews). Так широкое фото не режется в VK-ленте (жалоба
    пользователя 2026-07-05: "VK фотка обрубленная") — оно становится квадратным и
    показывается целиком везде. Если фото уже нужного соотношения — возвращаем как есть."""
    if ratio_key not in ASPECT_RATIOS:
        raise WatermarkError(f"Неизвестное соотношение сторон: {ratio_key}")

    ratio_w, ratio_h = ASPECT_RATIOS[ratio_key]
    target_ratio = ratio_w / ratio_h
    width, height = image.size
    current_ratio = width / height

    if abs(current_ratio - target_ratio) < 0.01:
        return image

    if current_ratio > target_ratio:
        canvas_w, canvas_h = width, round(width / target_ratio)
    else:
        canvas_w, canvas_h = round(height * target_ratio), height

    scale = max(canvas_w / width, canvas_h / height)
    bg = image.convert("RGB").resize((round(width * scale) + 1, round(height * scale) + 1))
    left = (bg.width - canvas_w) // 2
    top = (bg.height - canvas_h) // 2
    bg = bg.crop((left, top, left + canvas_w, top + canvas_h))
    bg = bg.filter(ImageFilter.GaussianBlur(blur_radius))
    bg = ImageEnhance.Brightness(bg).enhance(0.6)  # затемняем фон, чтобы фото выделялось

    canvas = bg.convert("RGBA")
    canvas.paste(image, ((canvas_w - width) // 2, (canvas_h - height) // 2), image)
    return canvas


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
