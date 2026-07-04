"""Watermark на видео через ffmpeg (аналог app/core/images/watermark.py для фото —
Pillow не умеет работать с видеопотоками, поэтому композит логотипа делает ffmpeg,
а геометрия позиции/масштаба считается в Python, как и для фото)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from app.config.loader import WatermarkConfig
from app.core.media.uniquifier import build_post_video_uniquify_filter
from app.paths import OUTPUT_DIR, PROJECT_ROOT


class VideoWatermarkError(Exception):
    """Логотип не найден, ffmpeg/ffprobe не установлены или упали — не тихий fallback."""


def build_overlay_position_expr(position: str, margin_px: int) -> tuple[str, str]:
    """main_w/main_h — размеры видео (первый вход overlay), overlay_w/overlay_h — логотипа."""
    x = str(margin_px) if "left" in position else f"main_w-overlay_w-{margin_px}"
    y = str(margin_px) if "top" in position else f"main_h-overlay_h-{margin_px}"
    return x, y


def build_filter_complex(
    *,
    logo_width_px: int,
    opacity_percent: int,
    position: str,
    margin_px: int,
    uniquify_filter: str | None = None,
) -> str:
    x_expr, y_expr = build_overlay_position_expr(position, margin_px)
    opacity = opacity_percent / 100

    base_label = "[0:v]"
    prefix = ""
    if uniquify_filter:
        prefix = f"[0:v]{uniquify_filter}[base];"
        base_label = "[base]"

    return (
        f"{prefix}[1:v]scale={logo_width_px}:-1,format=rgba,colorchannelmixer=aa={opacity}[wm];"
        f"{base_label}[wm]overlay={x_expr}:{y_expr}[out]"
    )


class VideoWatermarker:
    def __init__(self, config: WatermarkConfig, *, uniquify_enabled: bool = False) -> None:
        self._config = config
        self._uniquify_enabled = uniquify_enabled

    def apply(self, video_path: Path, *, post_id: int) -> Path:
        logo_path = PROJECT_ROOT / self._config.logo_path
        if not logo_path.exists():
            raise VideoWatermarkError(f"Логотип не найден: {logo_path}")

        for binary in ("ffmpeg", "ffprobe"):
            if shutil.which(binary) is None:
                raise VideoWatermarkError(f"{binary} не найден в PATH")

        video_width, video_height = _probe_dimensions(video_path)
        logo_width_px = int(video_width * self._config.size_ratio)
        # Уникализация встроена в тот же ffmpeg-проход (не отдельный ре-энкод) — быстрее
        # и не теряет качество дважды. Тот же смысл, что images.uniquify для фото:
        # снизить перцептивное совпадение с оригиналом поста, обходя антидубликат.
        uniquify_filter = (
            build_post_video_uniquify_filter(video_width, video_height, post_id)
            if self._uniquify_enabled
            else None
        )
        filter_complex = build_filter_complex(
            logo_width_px=logo_width_px,
            opacity_percent=self._config.opacity,
            position=self._config.position,
            margin_px=self._config.margin_px,
            uniquify_filter=uniquify_filter,
        )

        output_path = self._output_path(video_path, post_id)
        # crf 18 + preset slow — визуально почти без потерь (не роняем качество исходника).
        # Разрешение и fps исходника сохраняются (не масштабируем вниз). Аудио копируется
        # как есть. Так watermark не деградирует медиа, что и требовалось.
        command = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(logo_path),
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-crf", "18",
            "-preset", "slow",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            str(output_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise VideoWatermarkError(f"ffmpeg завершился с ошибкой: {result.stderr[-2000:]}")
        return output_path

    def _output_path(self, video_path: Path, post_id: int) -> Path:
        output_dir = OUTPUT_DIR / "videos" / str(post_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir / video_path.name


def _probe_dimensions(video_path: Path) -> tuple[int, int]:
    command = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json",
        str(video_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise VideoWatermarkError(f"ffprobe завершился с ошибкой: {result.stderr[-2000:]}")
    stream = json.loads(result.stdout)["streams"][0]
    return stream["width"], stream["height"]
