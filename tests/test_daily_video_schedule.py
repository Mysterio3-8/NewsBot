"""Догоняющее расписание дневного видео (баг 19.07.2026: рестарты съедали день)."""
from __future__ import annotations

import datetime

from app.headless_service import should_run_daily_video

WINDOW = {"start_hour_utc": 8, "window_minutes": 120}


def _at(day: int, hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 7, day, hour, minute)


def test_does_not_run_before_window_starts():
    assert should_run_daily_video(_at(21, 6), None, **WINDOW) is False


def test_runs_when_never_reposted_and_window_open():
    assert should_run_daily_video(_at(21, 11), None, **WINDOW) is True


def test_does_not_run_twice_in_one_day():
    assert should_run_daily_video(_at(21, 15), _at(21, 9), **WINDOW) is False


def test_catches_up_missed_day_immediately_after_restart():
    """19-го репоста не было; 20-го сервис поднялся вечером — фильм всё равно выходит."""
    assert should_run_daily_video(_at(20, 22), _at(18, 9), **WINDOW) is True


def test_start_moment_is_stable_across_restarts_within_same_day():
    day = _at(21, 0)
    offsets = {
        minute
        for minute in range(0, 180)
        if should_run_daily_video(day + datetime.timedelta(hours=8, minutes=minute), None, **WINDOW)
    }
    # Момент старта детерминирован датой: одна и та же граница при любом опросе.
    assert offsets == set(range(min(offsets), 180))
