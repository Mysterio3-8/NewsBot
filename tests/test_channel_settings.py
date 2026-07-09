"""Настройки канала (ChannelSettings) — парсинг Channel.settings_json."""
from app.core.channel_settings import ChannelSettings


def test_from_json_empty_returns_defaults():
    assert ChannelSettings.from_json(None) == ChannelSettings(filters_enabled=True)
    assert ChannelSettings.from_json("") == ChannelSettings(filters_enabled=True)
    assert ChannelSettings.from_json("{}").filters_enabled is True


def test_from_json_reads_filters_disabled():
    settings = ChannelSettings.from_json('{"filters_enabled": false}')
    assert settings.filters_enabled is False
    assert settings.max_posts_per_day is None


def test_from_json_reads_max_posts_per_day():
    settings = ChannelSettings.from_json('{"filters_enabled": false, "max_posts_per_day": 4}')
    assert settings.max_posts_per_day == 4


def test_to_json_roundtrip():
    original = ChannelSettings(filters_enabled=False, max_posts_per_day=3)
    assert ChannelSettings.from_json(original.to_json()) == original
