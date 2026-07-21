"""Нарезка фильма на короткие вертикальные клипы (9:16, 1080×1920) со случайных моментов.

Момент выбирается случайно (обычный ГПСЧ, криптостойкость не нужна), не пересекается
с ранее нарезанными участками этого же фильма и проверяется на жёлтую промо-плашку
«ищи фильм в комментариях» (покадрово, тем же цветовым детектором, что и фото).
Горизонтальный кадр вписывается в вертикаль с размытым фоном по краям (blur background).
"""
from __future__ import annotations

import datetime
import json
import logging
import random
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.core.images.promo_banner import has_promo_banner
from app.core.video.clip_overlay import render_clip_overlay

logger = logging.getLogger("monitoring")

CLIP_WIDTH = 1080
CLIP_HEIGHT = 1920
FRAME_CHECK_STEP_SECONDS = 5.0
MAX_PICK_ATTEMPTS = 40


class ClipCutError(Exception):
    """ffmpeg/ffprobe упали или чистый фрагмент не найден — не тихий fallback."""


@dataclass(frozen=True)
class ClipCut:
    start_seconds: float
    end_seconds: float
    path: Path


def probe_duration_seconds(video_path: Path) -> float:
    command = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(video_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise ClipCutError(f"ffprobe завершился с ошибкой: {result.stderr[-2000:]}")
    return float(json.loads(result.stdout)["format"]["duration"])


def overlaps(
    start: float, end: float, intervals: list[tuple[float, float]], min_gap: float
) -> bool:
    """True — [start, end] пересекается (с учётом зазора min_gap) с одним из интервалов."""
    return any(start < i_end + min_gap and end > i_start - min_gap for i_start, i_end in intervals)


def pick_segments(
    duration: float,
    *,
    clip_seconds: float,
    count: int,
    existing: list[tuple[float, float]],
    min_gap: float,
    is_segment_clean: Callable[[float], bool],
    rng: random.Random | None = None,
    max_attempts: int = MAX_PICK_ATTEMPTS,
) -> list[tuple[float, float]]:
    """До `count` непересекающихся сегментов длиной clip_seconds: случайное начало,
    конец не выходит за фильм, зазор min_gap от existing и друг от друга, кадры чистые
    (is_segment_clean(start) — проверка плашки, инжектится для тестов без ffmpeg)."""
    rng = rng or random.Random()
    if duration <= clip_seconds:
        return []
    picked: list[tuple[float, float]] = []
    attempts = 0
    while len(picked) < count and attempts < max_attempts:
        attempts += 1
        start = rng.uniform(0, duration - clip_seconds)
        end = start + clip_seconds
        if overlaps(start, end, existing + picked, min_gap):
            continue
        if not is_segment_clean(start):
            logger.info("Клип %.0f-%.0fс: найдена плашка, выбираю другой момент", start, end)
            continue
        picked.append((start, end))
    return picked


def segment_has_banner(video_path: Path, start: float, clip_seconds: float) -> bool:
    """Проверить кадры фрагмента (каждые FRAME_CHECK_STEP_SECONDS) на промо-плашку."""
    offsets = [start + t for t in _frame_offsets(clip_seconds)]
    with tempfile.TemporaryDirectory(prefix="clip_frames_") as tmp:
        for index, offset in enumerate(offsets):
            frame = Path(tmp) / f"frame_{index}.jpg"
            command = [
                "ffmpeg", "-y", "-v", "error",
                "-ss", f"{offset:.2f}",
                "-i", str(video_path),
                "-frames:v", "1",
                "-q:v", "4",
                str(frame),
            ]
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode != 0 or not frame.exists():
                continue  # кадр не извлёкся (конец файла и т.п.) — не считаем плашкой
            if has_promo_banner(frame):
                return True
    return False


def _frame_offsets(clip_seconds: float) -> list[float]:
    offsets = [0.0]
    t = FRAME_CHECK_STEP_SECONDS
    while t < clip_seconds - 1:
        offsets.append(t)
        t += FRAME_CHECK_STEP_SECONDS
    offsets.append(max(clip_seconds - 1, 0.0))
    return offsets


def build_vertical_filter() -> str:
    """Фон — кадр, растянутый на 1080×1920 и размытый; поверх — кадр целиком, вписанный
    без обрезки. Горизонтальное видео получает blur сверху/снизу, важное не режется."""
    return (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={CLIP_WIDTH}:{CLIP_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={CLIP_WIDTH}:{CLIP_HEIGHT},boxblur=20:5[bgb];"
        f"[fg]scale={CLIP_WIDTH}:{CLIP_HEIGHT}:force_original_aspect_ratio=decrease[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2[out]"
    )


def build_clip_filename(title: str, moment: datetime.datetime) -> str:
    """«Название фильма_YYYYMMDD_HHMMSS.mp4» — недопустимые для файловой системы
    символы убираются, пустое название → clip."""
    clean = re.sub(r'[\\/:*?"<>|]+', " ", title).strip()
    clean = re.sub(r"\s+", " ", clean) or "clip"
    return f"{clean}_{moment.strftime('%Y%m%d_%H%M%S')}.mp4"


def build_overlay_filter() -> str:
    """Тот же вертикальный кадр, но поверх — готовый прозрачный PNG (логотип + хук),
    отмасштабированный ровно под клип."""
    return (
        build_vertical_filter().replace("[out]", "[vert]")
        + f";[1:v]scale={CLIP_WIDTH}:{CLIP_HEIGHT}[ovl];[vert][ovl]overlay=0:0[out]"
    )


def cut_clip(
    video_path: Path,
    *,
    start: float,
    clip_seconds: float,
    out_path: Path,
    overlay_path: Path | None = None,
) -> Path:
    """Вырезать фрагмент и сконвертировать в вертикаль одним проходом ffmpeg.
    Перекодирование неизбежно (масштаб+blur-фон), поэтому preset veryfast — на слабом
    CPU VPS 35-секундный клип режется за минуты, не часы."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    overlay_args = ["-i", str(overlay_path)] if overlay_path is not None else []
    filter_complex = build_overlay_filter() if overlay_path is not None else build_vertical_filter()
    command = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{start:.2f}",
        "-t", f"{clip_seconds:.2f}",
        "-i", str(video_path),
        *overlay_args,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(out_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise ClipCutError(f"ffmpeg завершился с ошибкой: {result.stderr[-2000:]}")
    return out_path


def _build_overlay(
    out_dir: Path, stem: str, *, headline: list[str], logo_path: Path | None
) -> Path | None:
    """PNG-оформление клипа. Без логотипа оформление не рисуем вовсе; ошибка рендера
    не должна ронять нарезку — клип без надписи лучше, чем отсутствие клипа."""
    if logo_path is None:
        return None
    try:
        return render_clip_overlay(
            headline=headline[0] if headline else "",
            logo_path=logo_path,
            out_path=out_dir / f"{stem}_overlay.png",
            width=CLIP_WIDTH,
            height=CLIP_HEIGHT,
        )
    except Exception:
        logger.exception("Оформление клипа %s не построено, режу без него", stem)
        return None


def cut_clips(
    video_path: Path,
    *,
    title: str,
    out_dir: Path,
    clip_seconds: float,
    count: int,
    existing_intervals: list[tuple[float, float]],
    min_gap_seconds: float,
    headlines: list[str] | None = None,
    logo_path: Path | None = None,
    rng: random.Random | None = None,
) -> list[ClipCut]:
    """Полный цикл: длительность → выбор чистых сегментов → нарезка. Может вернуть
    меньше count клипов (фильм короткий/плашка повсюду) — это не ошибка, логируется.
    headlines[i] — хук поверх i-го клипа; вместе с logo_path включает оформление."""
    duration = probe_duration_seconds(video_path)
    segments = pick_segments(
        duration,
        clip_seconds=clip_seconds,
        count=count,
        existing=existing_intervals,
        min_gap=min_gap_seconds,
        is_segment_clean=lambda start: not segment_has_banner(video_path, start, clip_seconds),
        rng=rng,
    )
    if len(segments) < count:
        logger.warning(
            "Нарезка %s: выбрано %d/%d чистых сегментов", video_path.name, len(segments), count
        )
    cuts: list[ClipCut] = []
    base_moment = datetime.datetime.now()
    for index, (start, end) in enumerate(segments):
        # +index секунд — чтобы имена клипов одного запуска не совпадали
        moment = base_moment + datetime.timedelta(seconds=index)
        out_path = out_dir / build_clip_filename(title, moment)
        overlay_path = _build_overlay(
            out_dir, out_path.stem, headline=(headlines or [])[index:index + 1], logo_path=logo_path
        )
        cut_clip(
            video_path,
            start=start,
            clip_seconds=clip_seconds,
            out_path=out_path,
            overlay_path=overlay_path,
        )
        if overlay_path is not None:
            overlay_path.unlink(missing_ok=True)
        cuts.append(ClipCut(start_seconds=start, end_seconds=end, path=out_path))
        logger.info("Клип готов: %s (%.0f-%.0fс)", out_path.name, start, end)
    return cuts
