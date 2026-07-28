"""Автоуборка временных медиа: возраст, пустые каталоги, отчёт."""
from __future__ import annotations

import os
import time

from app.core.maintenance.cleanup import (
    MEDIA_RETENTION_HOURS,
    cleanup_directory,
    cleanup_output,
    format_disk_report,
    is_expired,
)


def _aged_file(path, hours_old: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 1024)
    old = time.time() - hours_old * 3600
    os.utime(path, (old, old))
    return path


def test_fresh_file_is_not_expired(tmp_path):
    path = _aged_file(tmp_path / "fresh.jpg", hours_old=1)

    assert is_expired(path, max_age_hours=24, now=time.time()) is False


def test_old_file_is_expired(tmp_path):
    path = _aged_file(tmp_path / "old.jpg", hours_old=48)

    assert is_expired(path, max_age_hours=24, now=time.time()) is True


def test_missing_file_is_not_expired(tmp_path):
    assert is_expired(tmp_path / "gone.jpg", max_age_hours=1, now=time.time()) is False


def test_cleanup_removes_only_old_files(tmp_path):
    _aged_file(tmp_path / "old.jpg", hours_old=48)
    fresh = _aged_file(tmp_path / "fresh.jpg", hours_old=1)

    result = cleanup_directory(tmp_path, max_age_hours=24)

    assert result.removed_files == 1
    assert result.freed_bytes == 1024
    assert fresh.exists()


def test_cleanup_removes_emptied_subdirs(tmp_path):
    _aged_file(tmp_path / "post_42" / "photo.jpg", hours_old=48)

    cleanup_directory(tmp_path, max_age_hours=24)

    assert not (tmp_path / "post_42").exists()


def test_cleanup_missing_directory_is_noop(tmp_path):
    result = cleanup_directory(tmp_path / "nope", max_age_hours=24)

    assert result.removed_files == 0


def test_cleanup_output_walks_all_known_dirs(tmp_path):
    """Фильм-сирота (6 ч) убирается, а свежий клип (порог 48 ч) остаётся."""
    _aged_file(tmp_path / "daily_video" / "film.mp4", hours_old=8)
    clip = _aged_file(tmp_path / "clips" / "clip.mp4", hours_old=8)
    _aged_file(tmp_path / "tg_raw_media" / "photo.jpg", hours_old=30)

    result = cleanup_output(tmp_path)

    assert result.removed_files == 2
    assert clip.exists()


def test_retention_covers_the_dirs_that_filled_the_disk():
    for name in ("tg_raw_media", "vk_raw_media", "daily_video", "images", "videos"):
        assert name in MEDIA_RETENTION_HOURS


def test_disk_report_lists_sizes(tmp_path):
    _aged_file(tmp_path / "tg_raw_media" / "a.jpg", hours_old=1)

    report = format_disk_report(tmp_path)

    assert "tg_raw_media" in report
    assert "1 файлов" in report
