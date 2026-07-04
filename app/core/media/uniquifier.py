"""AI-уникализатор медиа: из одного файла делает N визуально идентичных, но уникальных
по перцептивному хэшу вариантов — для перезаливов без потери качества.

Каждый вариант: набор незаметных глазу трансформаций (микро-сдвиг цвета/яркости,
крошечный зум-кроп, микро-изменение скорости для видео, зеркало опционально) + полная
очистка метаданных + высококачественный энкод (crf 18 / JPEG q95). Разрешение и fps
исходника сохраняются — качество НЕ понижается.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageEnhance

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


class MediaUniquifyError(Exception):
    """ffmpeg/ffprobe не найдены, не тот формат, или обработка упала — не тихий fallback."""


_NONZERO_OFFSETS = [-3, -2, -1, 1, 2, 3]


def build_post_video_uniquify_filter(width: int, height: int, post_id: int) -> str:
    """Одна (не N) незаметная трансформация видео, детерминированно зависящая от
    post_id — для встроенного шага в прод-пайплайне публикации (в отличие от
    generate_video_variants, который делает N вариантов для ручных перезаливов в боте).
    Тот же принцип, что уже применяется к фото (images.uniquify), расширенный на видео:
    микро eq (яркость/контраст/насыщенность/гамма) + крошечный кроп-и-масштаб обратно."""
    offset = _NONZERO_OFFSETS[post_id % len(_NONZERO_OFFSETS)]
    saturation_offset = _NONZERO_OFFSETS[(post_id // 7) % len(_NONZERO_OFFSETS)]
    brightness = round(offset * 0.004, 4)
    contrast = round(1 + offset * 0.006, 4)
    saturation = round(1 + saturation_offset * 0.01, 4)
    gamma = round(1 + offset * 0.003, 4)
    crop_px = abs(offset)
    return (
        f"eq=brightness={brightness}:contrast={contrast}:saturation={saturation}:gamma={gamma},"
        f"crop={width - 2 * crop_px}:{height - 2 * crop_px}:{crop_px}:{crop_px},"
        f"scale={width}:{height}"
    )


def uniquify_media(input_path: Path, *, count: int, output_dir: Path) -> list[Path]:
    """Диспетчер по типу файла: видео → ffmpeg-варианты, фото → Pillow-варианты."""
    if is_video(input_path):
        return generate_video_variants(input_path, count=count, output_dir=output_dir)
    if is_image(input_path):
        return generate_image_variants(input_path, count=count, output_dir=output_dir)
    raise MediaUniquifyError(f"Неподдерживаемый формат: {input_path.suffix}")


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_SUFFIXES


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_SUFFIXES


def build_video_variant_filter(width: int, height: int, index: int, count: int) -> tuple[str, float]:
    """Незаметные трансформации, детерминированно зависящие от index — 5 вызовов дают
    5 разных выходов. Возвращает (video-filter, speed) — speed нужен и для аудио."""
    center = (count - 1) / 2
    offset = index - center
    brightness = round(offset * 0.006, 4)
    contrast = round(1 + offset * 0.008, 4)
    saturation = round(1 + ((index % 3) - 1) * 0.02, 4)
    gamma = round(1 + offset * 0.004, 4)
    speed = round(1 + offset * 0.003, 4)
    crop_px = index  # 0 у первого варианта, дальше по 1px с каждой стороны

    parts: list[str] = []
    if speed != 1.0:
        parts.append(f"setpts=PTS/{speed}")
    parts.append(
        f"eq=brightness={brightness}:contrast={contrast}:saturation={saturation}:gamma={gamma}"
    )
    if crop_px > 0:
        parts.append(f"crop={width - 2 * crop_px}:{height - 2 * crop_px}:{crop_px}:{crop_px}")
        parts.append(f"scale={width}:{height}")
    return ",".join(parts), speed


def generate_video_variants(input_path: Path, *, count: int, output_dir: Path) -> list[Path]:
    _require_ffmpeg()
    width, height = _probe_dimensions(input_path)
    audio = _has_audio(input_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    variants: list[Path] = []
    for index in range(count):
        vf, speed = build_video_variant_filter(width, height, index, count)
        output_path = output_dir / f"{input_path.stem}_unique_{index + 1}.mp4"
        command = ["ffmpeg", "-y", "-i", str(input_path), "-vf", vf]
        if audio and speed != 1.0:
            command += ["-af", f"atempo={speed}", "-c:a", "aac"]
        elif audio:
            command += ["-c:a", "aac"]
        command += [
            "-c:v", "libx264", "-crf", "18", "-preset", "slow", "-pix_fmt", "yuv420p",
            "-map_metadata", "-1",  # полная очистка метаданных
            str(output_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise MediaUniquifyError(f"ffmpeg (вариант {index + 1}) упал: {result.stderr[-1500:]}")
        variants.append(output_path)
    return variants


def generate_image_variants(input_path: Path, *, count: int, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    variants: list[Path] = []
    for index in range(count):
        image = Image.open(input_path).convert("RGB")
        image = _apply_image_variant(image, index, count)
        output_path = output_dir / f"{input_path.stem}_unique_{index + 1}.jpg"
        # Без exif=... — метаданные не переносятся; quality=95 — почти без потерь.
        image.save(output_path, "JPEG", quality=95, subsampling=0)
        variants.append(output_path)
    return variants


def _apply_image_variant(image: Image.Image, index: int, count: int) -> Image.Image:
    center = (count - 1) / 2
    offset = index - center
    image = ImageEnhance.Brightness(image).enhance(1 + offset * 0.01)
    image = ImageEnhance.Contrast(image).enhance(1 + offset * 0.012)
    image = ImageEnhance.Color(image).enhance(1 + ((index % 3) - 1) * 0.03)

    crop_px = index
    if crop_px > 0:
        width, height = image.size
        image = image.crop((crop_px, crop_px, width - crop_px, height - crop_px))
        image = image.resize((width, height))
    return image


def _require_ffmpeg() -> None:
    for binary in ("ffmpeg", "ffprobe"):
        if shutil.which(binary) is None:
            raise MediaUniquifyError(f"{binary} не найден в PATH")


def _probe_dimensions(path: Path) -> tuple[int, int]:
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "json", str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise MediaUniquifyError(f"ffprobe упал: {result.stderr[-1500:]}")
    stream = json.loads(result.stdout)["streams"][0]
    return stream["width"], stream["height"]


def _has_audio(path: Path) -> bool:
    command = [
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=index", "-of", "json", str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        return False
    return bool(json.loads(result.stdout).get("streams"))
