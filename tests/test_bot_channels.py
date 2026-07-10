"""Управление каналами через бота: чистые функции (render/toggle/set) + сборка клавиатур."""
from __future__ import annotations

import app.bot_keyboards as kb
import app.control_bot as bot
from app.core.channel_settings import ChannelSettings
from app.db.repository import Repository, init_db, make_engine


def make_repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    return Repository(engine)


def _cinema(repo: Repository):
    return repo.create_channel(
        name="КиноЛайф",
        vk_destination="240120678",
        settings_json=ChannelSettings(
            filters_enabled=False, max_posts_per_day=3, min_interval_minutes=480
        ).to_json(),
    )


def test_render_channels_lists_all(tmp_path):
    repo = make_repo(tmp_path)
    _cinema(repo)
    text = bot.render_channels(repo)
    assert "КиноЛайф" in text


def test_render_channel_card_shows_settings(tmp_path):
    repo = make_repo(tmp_path)
    ch = _cinema(repo)
    repo.create_source(type="vk", name="Кинопремьеры", url="58170807", channel_id=ch.id)
    card = bot.render_channel_card(repo, ch.id)
    assert "КиноЛайф" in card
    assert "240120678" in card
    assert "выкл (лить всё)" in card  # filters_enabled=False
    assert "Источников: 1" in card


def test_toggle_channel_flips_enabled(tmp_path):
    repo = make_repo(tmp_path)
    ch = _cinema(repo)
    assert repo.get_channel(ch.id).enabled is True
    bot.toggle_channel(repo, ch.id)
    assert repo.get_channel(ch.id).enabled is False
    bot.toggle_channel(repo, ch.id)
    assert repo.get_channel(ch.id).enabled is True


def test_toggle_channel_filter_flips(tmp_path):
    repo = make_repo(tmp_path)
    ch = _cinema(repo)
    bot.toggle_channel_filter(repo, ch.id)  # было False → True
    assert ChannelSettings.from_json(repo.get_channel(ch.id).settings_json).filters_enabled is True


def test_set_channel_setting_updates_limit(tmp_path):
    repo = make_repo(tmp_path)
    ch = _cinema(repo)
    bot.set_channel_setting(repo, ch.id, "maxposts", "5")
    assert ChannelSettings.from_json(repo.get_channel(ch.id).settings_json).max_posts_per_day == 5


def test_set_channel_setting_rejects_non_number(tmp_path):
    repo = make_repo(tmp_path)
    ch = _cinema(repo)
    result = bot.set_channel_setting(repo, ch.id, "maxposts", "abc")
    assert "число" in result.lower()
    # значение не изменилось
    assert ChannelSettings.from_json(repo.get_channel(ch.id).settings_json).max_posts_per_day == 3


def test_channels_menu_has_button_per_channel(tmp_path):
    repo = make_repo(tmp_path)
    _cinema(repo)
    markup = kb.channels_menu(repo.list_channels())
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert any("КиноЛайф" in label for label in labels)


def test_channel_card_menu_reflects_enabled_state(tmp_path):
    repo = make_repo(tmp_path)
    ch = _cinema(repo)
    settings = ChannelSettings.from_json(ch.settings_json)
    markup = kb.channel_card_menu(ch, settings)
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert any("Выключить канал" in label for label in labels)  # enabled=True
    assert any("Лимит/день: 3" in label for label in labels)
