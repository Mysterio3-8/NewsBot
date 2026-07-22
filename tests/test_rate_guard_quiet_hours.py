"""Ночная пауза антибан-стопора (VK: 6-8ч тишины ночью)."""
from __future__ import annotations

import datetime

from app.core.channel_settings import ChannelSettings
from app.core.publishing.rate_guard import MSK_OFFSET_HOURS, is_quiet_now


def _utc_at_msk_hour(msk_hour: int) -> datetime.datetime:
    """UTC-момент, соответствующий заданному часу МСК."""
    return datetime.datetime(2026, 7, 22, (msk_hour - MSK_OFFSET_HOURS) % 24, 30)


def test_quiet_disabled_when_bounds_none():
    assert is_quiet_now(_utc_at_msk_hour(3), None, None) is False
    assert is_quiet_now(_utc_at_msk_hour(3), 0, None) is False
    assert is_quiet_now(_utc_at_msk_hour(3), 5, 5) is False


def test_quiet_window_within_day():
    # окно 0..7 МСК
    assert is_quiet_now(_utc_at_msk_hour(3), 0, 7) is True
    assert is_quiet_now(_utc_at_msk_hour(0), 0, 7) is True
    assert is_quiet_now(_utc_at_msk_hour(7), 0, 7) is False  # верхняя граница исключена
    assert is_quiet_now(_utc_at_msk_hour(12), 0, 7) is False


def test_quiet_window_crossing_midnight():
    # окно 23..6 МСК (через полночь)
    assert is_quiet_now(_utc_at_msk_hour(23), 23, 6) is True
    assert is_quiet_now(_utc_at_msk_hour(2), 23, 6) is True
    assert is_quiet_now(_utc_at_msk_hour(6), 23, 6) is False
    assert is_quiet_now(_utc_at_msk_hour(15), 23, 6) is False


def test_channel_settings_roundtrip_quiet_hours():
    settings = ChannelSettings(quiet_start_hour=0, quiet_end_hour=7)
    restored = ChannelSettings.from_json(settings.to_json())
    assert restored.quiet_start_hour == 0
    assert restored.quiet_end_hour == 7


def test_channel_settings_omits_quiet_when_unset():
    assert "quiet_start_hour" not in ChannelSettings().to_json()
