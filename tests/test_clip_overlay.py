"""Оформление клипов: перенос хука по словам, рендер оверлея, фильтр ffmpeg."""
from __future__ import annotations

from PIL import Image

from app.core.video.clip_cutter import build_overlay_filter, build_vertical_filter
from app.core.video.clip_overlay import MAX_HEADLINE_LINES, render_clip_overlay, wrap_lines


def _measure(char_width: int):
    return lambda line: len(line) * char_width


def test_wrap_lines_breaks_on_word_boundary():
    lines = wrap_lines("ОН НЕ ДОЛЖЕН БЫЛ ВЫЖИТЬ", max_width_px=100, measure=_measure(10))

    assert lines == ["ОН НЕ", "ДОЛЖЕН БЫЛ", "ВЫЖИТЬ"]


def test_wrap_lines_keeps_single_line_when_it_fits():
    assert wrap_lines("КОРОТКИЙ ХУК", max_width_px=1000, measure=_measure(10)) == ["КОРОТКИЙ ХУК"]


def test_wrap_lines_limits_line_count():
    long_text = " ".join(["СЛОВО"] * 20)

    assert len(wrap_lines(long_text, max_width_px=60, measure=_measure(10))) == MAX_HEADLINE_LINES


def test_render_clip_overlay_draws_logo_and_text_on_transparent_canvas(tmp_path):
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (200, 100), (255, 0, 0, 255)).save(logo)

    out = render_clip_overlay(
        headline="он не должен был выжить",
        logo_path=logo,
        out_path=tmp_path / "overlay.png",
        width=1080,
        height=1920,
    )

    image = Image.open(out)
    assert image.size == (1080, 1920)
    assert image.mode == "RGBA"
    # Низ кадра остаётся полностью прозрачным — оформление только сверху.
    assert image.getpixel((540, 1900))[3] == 0
    assert image.getbbox() is not None


def test_render_clip_overlay_without_headline_still_places_logo(tmp_path):
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (200, 100), (0, 255, 0, 255)).save(logo)

    out = render_clip_overlay(
        headline="", logo_path=logo, out_path=tmp_path / "o.png", width=1080, height=1920
    )

    image = Image.open(out)
    right_top_alpha = image.getpixel((1080 - 49 - 100, 60))[3]
    assert right_top_alpha == 255


def test_overlay_filter_extends_vertical_filter_with_second_input():
    plain = build_vertical_filter()
    with_overlay = build_overlay_filter()

    assert with_overlay.startswith(plain.replace("[out]", "[vert]"))
    assert "[1:v]scale=1080:1920[ovl]" in with_overlay
    assert with_overlay.endswith("[vert][ovl]overlay=0:0[out]")
