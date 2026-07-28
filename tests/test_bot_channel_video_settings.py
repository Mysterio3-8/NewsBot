"""Управление видео-настройками канала из бота (ТЗ 2026-07-28)."""
from __future__ import annotations

from app.bot_keyboards import channel_card_menu
from app.control_bot import set_channel_setting
from app.core.channel_settings import ChannelSettings
from app.db.repository import Repository, init_db, make_engine


def _repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "bot.db")
    init_db(engine)
    return Repository(engine)


def _video_channel(repo):
    settings = ChannelSettings(
        daily_video_youtube_channels=["https://www.youtube.com/@x"],
        daily_video_count=3,
        daily_clip_count=1,
    )
    return repo.create_channel(name="Кино", vk_destination="1", settings_json=settings.to_json())


def test_set_films_per_day(tmp_path):
    repo = _repo(tmp_path)
    channel = _video_channel(repo)

    result = set_channel_setting(repo, channel.id, "films", "3")

    assert "фильмов/день = 3" in result
    updated = ChannelSettings.from_json(repo.get_channel(channel.id).settings_json)
    assert updated.daily_video_count == 3


def test_set_clips_per_film(tmp_path):
    repo = _repo(tmp_path)
    channel = _video_channel(repo)

    set_channel_setting(repo, channel.id, "clips", "2")

    updated = ChannelSettings.from_json(repo.get_channel(channel.id).settings_json)
    assert updated.daily_clip_count == 2


def test_non_numeric_value_rejected(tmp_path):
    repo = _repo(tmp_path)
    channel = _video_channel(repo)

    assert "число" in set_channel_setting(repo, channel.id, "films", "три")


def test_video_buttons_shown_for_video_channel(tmp_path):
    repo = _repo(tmp_path)
    channel = _video_channel(repo)
    settings = ChannelSettings.from_json(channel.settings_json)

    texts = [b.text for row in channel_card_menu(channel, settings).inline_keyboard for b in row]

    assert any("Фильмов/день: 3" in t for t in texts)
    assert any("Клипов на фильм: 1" in t for t in texts)


def test_video_buttons_hidden_for_plain_channel(tmp_path):
    repo = _repo(tmp_path)
    channel = repo.create_channel(name="Новости", vk_destination="2", settings_json="{}")
    settings = ChannelSettings.from_json(channel.settings_json)

    texts = [b.text for row in channel_card_menu(channel, settings).inline_keyboard for b in row]

    assert not any("Фильмов/день" in t for t in texts)
