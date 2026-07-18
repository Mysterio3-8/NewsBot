"""Нарезка клипов: чистая логика выбора сегментов, имена файлов, фильтр 9:16."""
import datetime
import random

from app.core.video.clip_cutter import (
    build_clip_filename,
    build_vertical_filter,
    overlaps,
    pick_segments,
)


def test_overlaps_detects_intersection_and_gap():
    intervals = [(100.0, 135.0)]

    assert overlaps(120.0, 155.0, intervals, min_gap=0) is True  # пересечение
    assert overlaps(140.0, 175.0, intervals, min_gap=0) is False  # рядом, но не задевает
    assert overlaps(140.0, 175.0, intervals, min_gap=10) is True  # зазор 10с нарушен
    assert overlaps(150.0, 185.0, intervals, min_gap=10) is False


def test_pick_segments_no_overlap_within_bounds():
    rng = random.Random(7)
    segments = pick_segments(
        7200,
        clip_seconds=35,
        count=3,
        existing=[],
        min_gap=120,
        is_segment_clean=lambda start: True,
        rng=rng,
    )

    assert len(segments) == 3
    for start, end in segments:
        assert 0 <= start and end <= 7200
        assert end - start == 35
    for i, seg in enumerate(segments):
        others = segments[:i] + segments[i + 1:]
        assert overlaps(seg[0], seg[1], others, min_gap=120) is False


def test_pick_segments_avoids_existing_intervals():
    rng = random.Random(1)
    existing = [(0.0, 3000.0), (3100.0, 7000.0)]  # почти весь фильм уже нарезан

    segments = pick_segments(
        7200,
        clip_seconds=35,
        count=3,
        existing=existing,
        min_gap=10,
        is_segment_clean=lambda start: True,
        rng=rng,
        max_attempts=500,
    )

    for start, end in segments:
        assert overlaps(start, end, existing, min_gap=10) is False


def test_pick_segments_rejects_dirty_segments():
    """Сегмент с плашкой пропускается — выбирается другой момент (ТЗ)."""
    rng = random.Random(3)
    dirty_before_1000 = lambda start: start >= 1000  # noqa: E731

    segments = pick_segments(
        7200,
        clip_seconds=35,
        count=2,
        existing=[],
        min_gap=60,
        is_segment_clean=dirty_before_1000,
        rng=rng,
        max_attempts=200,
    )

    assert len(segments) == 2
    assert all(start >= 1000 for start, _ in segments)


def test_pick_segments_returns_empty_for_too_short_video():
    segments = pick_segments(
        20,
        clip_seconds=35,
        count=3,
        existing=[],
        min_gap=60,
        is_segment_clean=lambda start: True,
    )
    assert segments == []


def test_build_clip_filename_format_and_sanitize():
    moment = datetime.datetime(2026, 7, 18, 14, 23, 15)

    assert build_clip_filename("Интерстеллар", moment) == "Интерстеллар_20260718_142315.mp4"
    assert build_clip_filename('Фильм: "Матрица"?', moment) == "Фильм Матрица_20260718_142315.mp4"
    assert build_clip_filename("", moment) == "clip_20260718_142315.mp4"


def test_build_vertical_filter_targets_1080x1920_with_blur_background():
    filter_complex = build_vertical_filter()

    assert "1080:1920" in filter_complex
    assert "boxblur" in filter_complex  # размытый фон, а не чёрные полосы
    assert "force_original_aspect_ratio=decrease" in filter_complex  # кадр не режется
