"""Чужая промо-плашка на кадре: детекция по цвету и переоформление.

Источники вроде «Кинопремьеры» лепят на кадры яркую жёлтую плашку с текстом
"СЕРИАЛ ИЩИ В КОММЕНТАРИЯХ" — чужое оформление, публиковать как есть нельзя.
Vision-детектор (Groq) на это ненадёжен (лимит 429). Плашка — плотный ярко-жёлтый
прямоугольник, детектится по цвету детерминированно.

Два режима работы (`ChannelSettings.promo_banner_mode`):

* `drop` — кадр с плашкой не берём вообще (поведение с 2026-07-18: раньше пытались
  спасти обрезкой до «чистой половины», и кусок плашки просачивался в публикацию);
* `restyle` — ТЗ владельца 2026-08-10: «плашка стоит сверху, а её берём и вниз ставим,
  и меняем на ней цвет, другой цвет букв и другой цвет плашки; если так тяжело
  сделать, то лучше вообще этот кадр не брать». Ровно это здесь и реализовано,
  включая последнюю оговорку: не получилось — возвращаем None, и кадр отбрасывается.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
from PIL import Image

_MIN_YELLOW_RATIO = 0.004
_MIN_FILL_RATIO = 0.45

# Переоформляем только плашку-ПОЛОСУ у края кадра: её можно вырезать целой строкой
# пикселей и приклеить к противоположному краю, не тронув сюжет. Плашка-«наклейка»
# посреди кадра так не переносится — под ней нечем закрыть дыру.
_MIN_BAR_WIDTH_RATIO = 0.55
_MAX_EDGE_OFFSET_RATIO = 0.04
"""Насколько плашка может не доходить до края. Держим маленьким намеренно: полоса
вырезается ОТ КРАЯ кадра, и всё, что осталось между краем и плашкой, перекрашивается
вместе с ней. Пара пикселей рамки — незаметно, десятая часть кадра — уже испорченный
сюжет."""
_MAX_BAR_HEIGHT_RATIO = 0.35

# Пары «фон, буквы»: тёмная подложка + светлый текст. Выбираются детерминированно по
# имени файла — один кадр всегда перекрашивается одинаково, что важно для тестов и для
# повторного прогона одного и того же поста.
_PALETTE: tuple[tuple[tuple[int, int, int], tuple[int, int, int]], ...] = (
    ((17, 24, 39), (245, 245, 245)),    # графит / белый
    ((124, 21, 21), (255, 236, 214)),   # тёмно-красный / кремовый
    ((12, 58, 74), (222, 245, 255)),    # тёмная бирюза / ледяной
    ((45, 20, 68), (238, 224, 255)),    # тёмный фиолет / сиреневый
    ((23, 60, 30), (232, 250, 226)),    # тёмно-зелёный / салатовый
)


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


def restyle_promo_banner(image_path: Path | str, out_path: Path | None = None) -> Path | None:
    """Плашку переносим к противоположному краю и перекрашиваем. Путь к новому файлу.

    None — переоформить не вышло (плашка не полоса, не у края, слишком высокая, файл
    не читается). Вызывающий обязан такой кадр выбросить, а не публиковать как есть.

    Плашки на кадре после переноса нет вовсе: исходная полоса ВЫРЕЗАЕТСЯ, а на её место
    встаёт остаток кадра. Размер кадра сохраняется — иначе в медиагруппе VK/TG соседние
    фото поедут по-разному."""
    path = Path(image_path)
    try:
        image = Image.open(path).convert("RGB")
    except Exception:
        return None

    pixels = np.asarray(image).astype(np.int16)
    bbox = _banner_bbox(pixels)
    if bbox is None:
        return None  # плашки нет — переоформлять нечего, звать эту функцию было незачем

    height, width = pixels.shape[0], pixels.shape[1]
    x0, y0, x1, y1 = bbox
    if (x1 - x0 + 1) < width * _MIN_BAR_WIDTH_RATIO:
        return None  # не полоса во всю ширину, а наклейка — переносить некуда
    if (y1 - y0 + 1) > height * _MAX_BAR_HEIGHT_RATIO:
        return None  # «плашка» на треть кадра — скорее жёлтый сюжет, не оформление

    at_top = y0 <= height * _MAX_EDGE_OFFSET_RATIO
    at_bottom = y1 >= height * (1 - _MAX_EDGE_OFFSET_RATIO)
    if at_top == at_bottom:
        return None  # ни к одному краю не прижата (или растянута на оба) — не наш случай

    # Полоса режется ОТ КРАЯ кадра, а не по bbox жёлтого: иначе тонкая рамка между
    # краем и плашкой осталась бы на месте, а кадр стал бы ниже исходного на её высоту
    # (в медиагруппе VK/TG соседние фото поехали бы по-разному).
    strip = pixels[: y1 + 1, :, :] if at_top else pixels[y0:, :, :]
    rest = pixels[y1 + 1 :, :, :] if at_top else pixels[:y0, :, :]
    bar = _recolor_bar(strip, _pick_palette(path))
    # Плашка уезжает к ПРОТИВОПОЛОЖНОМУ краю — этого и просил владелец.
    stacked = np.vstack([rest, bar] if at_top else [bar, rest])

    target = out_path or path.with_name(f"{path.stem}_banner{path.suffix}")
    Image.fromarray(stacked.astype(np.uint8), mode="RGB").save(target)
    return target


def _pick_palette(path: Path) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Цвета по хешу имени файла: тот же кадр — та же перекраска, разные кадры — разная."""
    digest = hashlib.sha1(path.name.encode("utf-8")).digest()
    return _PALETTE[digest[0] % len(_PALETTE)]


def _recolor_bar(
    bar: np.ndarray, palette: tuple[tuple[int, int, int], tuple[int, int, int]]
) -> np.ndarray:
    """Жёлтую полосу → полоса новых цветов с сохранением букв.

    Буквы берём не бинарной маской «не жёлтый», а плавной альфой по яркости: у полосы
    фон светлый (жёлтый), текст тёмный, и градация между ними — это сглаживание шрифта.
    Бинарная маска съела бы его, и буквы вышли бы рваными."""
    background, foreground = palette
    gray = bar.mean(axis=2)
    low, high = float(np.percentile(gray, 5)), float(np.percentile(gray, 95))
    alpha = np.clip((high - gray) / max(high - low, 1.0), 0.0, 1.0)[:, :, None]
    return np.asarray(background) * (1 - alpha) + np.asarray(foreground) * alpha
