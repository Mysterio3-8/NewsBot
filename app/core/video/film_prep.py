"""Единое оформление скачанного фильма перед публикацией и нарезкой.

Два действия одним проходом ffmpeg (два ре-энкода трёхчасового фильма на 1-ядерном
VPS недопустимы): замылить фиксированную область с чужим водяным знаком и наложить
свой логотип в правый верхний угол.

Область знака задаётся в ДОЛЯХ кадра, а не в пикселях: один и тот же канал отдаёт
видео в разном разрешении (480p/720p), и пиксельные координаты разъезжались бы.
Умный поиск логотипа не делаем — фиксированной области достаточно (ТЗ 2026-07-21).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger("monitoring")

LOGO_WIDTH_RATIO = 0.12
LOGO_MARGIN_RATIO = 0.03
LOGO_OPACITY = 0.85


class FilmPrepError(Exception):
    """ffmpeg не найден или упал — не тихий fallback."""


def build_blur_stage(region: list[float], strength: int, *, src: str, dst: str) -> str:
    """Замылить прямоугольник [x, y, w, h] (доли кадра): вырезаем область, блюрим,
    кладём обратно на то же место."""
    x, y, width, height = region
    return (
        f"[{src}]split=2[{dst}_bg][{dst}_crop];"
        # gblur, а не boxblur: у boxblur радиус ограничен размером плоскости, и на
        # маленькой области с водяным знаком ffmpeg падает "Invalid chroma_param radius".
        f"[{dst}_crop]crop=iw*{width}:ih*{height}:iw*{x}:ih*{y},"
        f"gblur=sigma={strength}:steps=2[{dst}_bl];"
        # В overlay нет iw/ih — только main_w/main_h (проверено живым ffmpeg).
        f"[{dst}_bg][{dst}_bl]overlay=main_w*{x}:main_h*{y}[{dst}]"
    )


def build_logo_stage(video_width: int, *, src: str, dst: str) -> str:
    """Логотип в правом верхнем углу — та же геометрия, что и на клипах."""
    logo_width = max(int(video_width * LOGO_WIDTH_RATIO), 1)
    margin = f"main_w*{LOGO_MARGIN_RATIO}"
    return (
        f"[1:v]scale={logo_width}:-1,format=rgba,colorchannelmixer=aa={LOGO_OPACITY}[{dst}_wm];"
        f"[{src}][{dst}_wm]overlay=main_w-overlay_w-{margin}:{margin}[{dst}]"
    )


def build_film_filter(
    *, video_width: int, logo: bool, blur_region: list[float] | None, blur_strength: int
) -> str:
    """Полная цепочка фильтров, метки не переиспользуются (ffmpeg этого не допускает).
    Пустая строка → делать нечего, фильм не трогаем."""
    stages: list[str] = []
    source = "0:v"
    if blur_region:
        stages.append(build_blur_stage(blur_region, blur_strength, src=source, dst="v1"))
        source = "v1"
    if logo:
        stages.append(build_logo_stage(video_width, src=source, dst="v2"))
        source = "v2"
    if not stages:
        return ""
    return ";".join(stages).replace(f"[{source}]", "[out]")


def prepare_film(
    video_path: Path,
    *,
    logo_path: Path | None,
    blur_region: list[float] | None,
    blur_strength: int,
    video_width: int,
) -> Path:
    """Вернуть путь к оформленному фильму. Нечего делать → исходный файл как есть
    (это нормальный путь, а не ошибка: перекодировать трёхчасовой фильм ради ничего —
    самое дорогое, что можно сделать на 1-ядерном VPS)."""
    filter_complex = build_film_filter(
        video_width=video_width,
        logo=logo_path is not None,
        blur_region=blur_region,
        blur_strength=blur_strength,
    )
    if not filter_complex:
        return video_path
    if shutil.which("ffmpeg") is None:
        raise FilmPrepError("ffmpeg не найден в PATH")

    out_path = video_path.with_name(f"{video_path.stem}_branded.mp4")
    logo_input = ["-i", str(logo_path)] if logo_path is not None else []
    # preset veryfast и crf 23: на 1 ядре VPS это ~3x реального времени (трёхчасовой
    # фильм ≈ 50 минут). preset slow, как у watermark постов, дал бы сутки на фильм.
    command = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(video_path),
        *logo_input,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(out_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise FilmPrepError(f"ffmpeg завершился с ошибкой: {result.stderr[-2000:]}")
    # Исходник больше не нужен, а диск VPS маленький — две копии фильма его добивают.
    video_path.unlink(missing_ok=True)
    logger.info("Фильм оформлен: %s", out_path.name)
    return out_path
