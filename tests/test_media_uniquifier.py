"""AI-уникализатор медиа. Видео-тесты реально вызывают ffmpeg/ffprobe (установлены на
машине), фото — Pillow. Проверяем: варианты создаются, качество/размер сохранены,
метаданных нет, варианты отличаются побайтово (значит и по перцептивному хэшу)."""
from __future__ import annotations

import subprocess

import pytest
from PIL import Image

from app.core.media.uniquifier import (
    MediaUniquifyError,
    build_post_video_uniquify_filter,
    build_video_variant_filter,
    generate_image_variants,
    generate_video_variants,
    is_image,
    is_video,
    uniquify_media,
)


def test_is_video_and_is_image_by_suffix(tmp_path):
    assert is_video(tmp_path / "clip.MP4") is True
    assert is_video(tmp_path / "photo.jpg") is False
    assert is_image(tmp_path / "photo.PNG") is True
    assert is_image(tmp_path / "clip.mp4") is False


def test_build_video_variant_filter_differs_per_index():
    a, _ = build_video_variant_filter(1920, 1080, 0, 5)
    b, _ = build_video_variant_filter(1920, 1080, 3, 5)
    assert a != b
    assert "eq=" in a


def test_generate_image_variants_creates_distinct_files_without_metadata(tmp_path):
    source = tmp_path / "src.jpg"
    Image.new("RGB", (800, 600), color=(120, 90, 60)).save(source)

    variants = generate_image_variants(source, count=5, output_dir=tmp_path / "out")

    assert len(variants) == 5
    for path in variants:
        assert path.exists()
        with Image.open(path) as img:
            assert img.size == (800, 600)  # разрешение сохранено
            assert not img.getexif()  # метаданных нет
    # варианты отличаются байтами (значит и хэшем)
    blobs = [p.read_bytes() for p in variants]
    assert len(set(blobs)) == 5


def test_generate_video_variants_creates_distinct_playable_files(tmp_path):
    source = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=640x360:rate=30",
         "-pix_fmt", "yuv420p", str(source)],
        check=True, capture_output=True,
    )

    variants = generate_video_variants(source, count=3, output_dir=tmp_path / "out")

    assert len(variants) == 3
    for path in variants:
        assert path.exists() and path.stat().st_size > 0
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        )
        assert '"width": 640' in probe.stdout  # разрешение исходника сохранено
    blobs = [p.read_bytes() for p in variants]
    assert len(set(blobs)) == 3


def test_generate_video_variants_raises_on_broken_input(tmp_path):
    source = tmp_path / "broken.mp4"
    source.write_bytes(b"not a video")
    with pytest.raises(MediaUniquifyError):
        generate_video_variants(source, count=2, output_dir=tmp_path / "out")


def test_uniquify_media_dispatches_image(tmp_path):
    source = tmp_path / "src.jpg"
    Image.new("RGB", (400, 300), color=(10, 20, 30)).save(source)
    variants = uniquify_media(source, count=5, output_dir=tmp_path / "out")
    assert len(variants) == 5


def test_build_post_video_uniquify_filter_differs_by_post_id():
    a = build_post_video_uniquify_filter(1920, 1080, post_id=1)
    b = build_post_video_uniquify_filter(1920, 1080, post_id=2)
    assert a != b
    assert "eq=" in a and "crop=" in a and "scale=1920:1080" in a


def test_build_post_video_uniquify_filter_deterministic_for_same_post_id():
    a = build_post_video_uniquify_filter(1920, 1080, post_id=42)
    b = build_post_video_uniquify_filter(1920, 1080, post_id=42)
    assert a == b


def test_uniquify_media_rejects_unsupported_format(tmp_path):
    source = tmp_path / "doc.txt"
    source.write_text("hello")
    with pytest.raises(MediaUniquifyError):
        uniquify_media(source, count=5, output_dir=tmp_path / "out")
