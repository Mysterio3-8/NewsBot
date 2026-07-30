"""Зазор между фильмами в минутах — нужен при большом daily_video_count
(24 фильма/сутки в целочасовой зазор не влезают)."""
from __future__ import annotations

import datetime

from app.core.channel_settings import ChannelSettings
from app.headless_service import should_run_daily_video


def test_gap_minutes_overrides_hours():
    settings = ChannelSettings(daily_video_min_gap_hours=5, daily_video_min_gap_minutes=30)
    assert settings.video_gap_minutes == 30


def test_gap_falls_back_to_hours():
    assert ChannelSettings(daily_video_min_gap_hours=4).video_gap_minutes == 240


def test_gap_minutes_roundtrip():
    settings = ChannelSettings(daily_video_min_gap_minutes=30)
    assert ChannelSettings.from_json(settings.to_json()).daily_video_min_gap_minutes == 30


def test_gap_omitted_when_unset():
    assert "daily_video_min_gap_minutes" not in ChannelSettings().to_json()


def test_second_film_allowed_after_minute_gap():
    now = datetime.datetime(2026, 7, 30, 12, 0)
    last = now - datetime.timedelta(minutes=31)
    assert should_run_daily_video(now, last, 1, per_day=24, min_gap_minutes=30) is True


def test_second_film_blocked_before_minute_gap():
    now = datetime.datetime(2026, 7, 30, 12, 0)
    last = now - datetime.timedelta(minutes=20)
    assert should_run_daily_video(now, last, 1, per_day=24, min_gap_minutes=30) is False


def test_daily_cap_still_enforced():
    now = datetime.datetime(2026, 7, 30, 12, 0)
    last = now - datetime.timedelta(hours=2)
    assert should_run_daily_video(now, last, 24, per_day=24, min_gap_minutes=30) is False


def test_hours_still_work_without_minutes():
    now = datetime.datetime(2026, 7, 30, 12, 0)
    last = now - datetime.timedelta(hours=6)
    assert should_run_daily_video(now, last, 1, per_day=3, min_gap_hours=5) is True
