"""Watermark для видео (app/core/video/watermark.py) — ffmpeg вместо Pillow.
Интеграционный тест реально вызывает ffmpeg/ffprobe (подтверждены установленными
на машине, см. NEXT_SESSION.md) — только юнит-тестов на билдер фильтра недостаточно,
т.к. история проекта уже показала, что реальный вызов внешнего инструмента ловит
баги, которые моки пропускают (см. "Известные грабли" в CLAUDE.md)."""
from __future__ import annotations

import subprocess

import pytest
from PIL import Image

from app.config.loader import WatermarkConfig
from app.core.video.watermark import (
    VideoWatermarker,
    VideoWatermarkError,
    build_filter_complex,
    build_overlay_position_expr,
)


def make_config(**overrides) -> WatermarkConfig:
    defaults = dict(
        logo_path="assets/test_logo.png",
        position="top-right",
        opacity=65,
        margin_px=20,
        size_ratio=0.2,
    )
    defaults.update(overrides)
    return WatermarkConfig(**defaults)


@pytest.mark.parametrize(
    "position,expected_x,expected_y",
    [
        ("top-right", "main_w-overlay_w-10", "10"),
        ("top-left", "10", "10"),
        ("bottom-right", "main_w-overlay_w-10", "main_h-overlay_h-10"),
        ("bottom-left", "10", "main_h-overlay_h-10"),
    ],
)
def test_build_overlay_position_expr(position, expected_x, expected_y):
    x, y = build_overlay_position_expr(position, 10)
    assert x == expected_x
    assert y == expected_y


def test_build_filter_complex_includes_scale_opacity_and_overlay():
    filter_complex = build_filter_complex(
        logo_width_px=150, opacity_percent=65, position="top-right", margin_px=20
    )
    assert "scale=150:-1" in filter_complex
    assert "aa=0.65" in filter_complex
    assert "overlay=main_w-overlay_w-20:20[out]" in filter_complex
    assert "[0:v][wm]overlay" in filter_complex  # без уникализации — база не тронута


def test_build_filter_complex_inserts_uniquify_filter_before_overlay():
    filter_complex = build_filter_complex(
        logo_width_px=150, opacity_percent=65, position="top-right", margin_px=20,
        uniquify_filter="eq=brightness=0.01",
    )
    assert filter_complex.startswith("[0:v]eq=brightness=0.01[base];")
    assert "[base][wm]overlay" in filter_complex


def test_watermarker_raises_when_logo_missing(tmp_path):
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"not a real video")

    watermarker = VideoWatermarker(make_config(logo_path="assets/does_not_exist.png"))

    with pytest.raises(VideoWatermarkError):
        watermarker.apply(video_path, post_id=1)


def test_watermarker_applies_logo_to_real_video_via_ffmpeg(tmp_path, monkeypatch):
    import app.core.video.watermark as watermark_module

    monkeypatch.setattr(watermark_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(watermark_module, "OUTPUT_DIR", tmp_path / "output")

    (tmp_path / "assets").mkdir()
    logo_path = tmp_path / "assets" / "logo.png"
    Image.new("RGBA", (200, 100), color=(255, 0, 0, 255)).save(logo_path)

    source_video_path = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10",
            "-pix_fmt", "yuv420p", str(source_video_path),
        ],
        check=True, capture_output=True,
    )

    watermarker = VideoWatermarker(make_config(logo_path="assets/logo.png"))
    output_path = watermarker.apply(source_video_path, post_id=1)

    assert output_path.exists()
    assert output_path.stat().st_size > 0

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", str(output_path)],
        capture_output=True, text=True, check=True,
    )
    assert '"width"' in probe.stdout


def test_watermarker_with_uniquify_enabled_preserves_resolution_and_differs_by_post_id(
    tmp_path, monkeypatch
):
    """Встроенная уникализация (не отдельная фича бота, а шаг прод-пайплайна) —
    разрешение сохраняется, а вывод для разных post_id отличается побайтово."""
    import app.core.video.watermark as watermark_module

    monkeypatch.setattr(watermark_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(watermark_module, "OUTPUT_DIR", tmp_path / "output")

    (tmp_path / "assets").mkdir()
    logo_path = tmp_path / "assets" / "logo.png"
    Image.new("RGBA", (200, 100), color=(255, 0, 0, 255)).save(logo_path)

    source_video_path = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10",
            "-pix_fmt", "yuv420p", str(source_video_path),
        ],
        check=True, capture_output=True,
    )

    watermarker = VideoWatermarker(make_config(logo_path="assets/logo.png"), uniquify_enabled=True)
    output_a = watermarker.apply(source_video_path, post_id=101)
    output_b = watermarker.apply(source_video_path, post_id=202)

    for output_path in (output_a, output_b):
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", str(output_path)],
            capture_output=True, text=True, check=True,
        )
        assert '"width": 320' in probe.stdout
        assert '"height": 240' in probe.stdout

    assert output_a.read_bytes() != output_b.read_bytes()
