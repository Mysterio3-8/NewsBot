"""Оформление фильма перед публикацией: блюр области знака + свой логотип."""
from __future__ import annotations

import subprocess

import pytest
from PIL import Image

from app.core.video.film_prep import build_film_filter, prepare_film

REGION = [0.8, 0.05, 0.15, 0.1]


def test_no_logo_and_no_region_means_nothing_to_do():
    assert build_film_filter(video_width=1280, logo=False, blur_region=None, blur_strength=20) == ""


def test_logo_only_chain_starts_from_source_and_ends_with_out():
    chain = build_film_filter(video_width=1280, logo=True, blur_region=None, blur_strength=20)

    assert "[0:v]" in chain
    assert chain.endswith("[out]")
    assert "[1:v]scale=153:-1" in chain


def test_blur_chain_crops_blurs_and_puts_region_back():
    chain = build_film_filter(video_width=1280, logo=False, blur_region=REGION, blur_strength=30)

    assert "crop=iw*0.15:ih*0.1:iw*0.8:ih*0.05" in chain
    assert "gblur=sigma=30:steps=2" in chain
    assert chain.endswith("[out]")


def test_blur_and_logo_chain_reuses_no_label_twice():
    chain = build_film_filter(video_width=1280, logo=True, blur_region=REGION, blur_strength=20)

    outputs = [part.rsplit("[", 1)[-1].rstrip("]") for part in chain.split(";")]
    assert len(outputs) == len(set(outputs)), f"метка использована дважды: {outputs}"


def test_prepare_film_returns_source_untouched_when_nothing_to_do(tmp_path):
    film = tmp_path / "film.mp4"
    film.write_bytes(b"video")

    result = prepare_film(
        film, logo_path=None, blur_region=None, blur_strength=20, video_width=1280
    )

    assert result == film
    assert film.exists()


@pytest.mark.skipif(
    subprocess.run(["ffmpeg", "-version"], capture_output=True).returncode != 0,
    reason="ffmpeg недоступен",
)
def test_prepare_film_produces_playable_video_with_real_ffmpeg(tmp_path):
    film = tmp_path / "film.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=10",
         "-t", "2", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(film)],
        check=True,
    )
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (100, 50), (255, 0, 0, 255)).save(logo)

    result = prepare_film(
        film, logo_path=logo, blur_region=REGION, blur_strength=10, video_width=640
    )

    assert result.exists() and result != film
    assert not film.exists()  # исходник удалён — диск VPS не держит две копии
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(result)],
        capture_output=True, text=True,
    )
    assert probe.stdout.strip() == "640,360"
