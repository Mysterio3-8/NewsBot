"""Единый пульт «📦 Софты»: чистые функции реестра (без aiogram)."""
from __future__ import annotations

from types import SimpleNamespace

import app.control_bot as bot


def _channels():
    return [
        SimpleNamespace(id=1, name="Новости", enabled=True),
        SimpleNamespace(id=5, name="Кино", enabled=False),
    ]


def test_build_soft_list_engine_first_then_channels_then_processes():
    softs = bot.build_soft_list(_channels(), [("p_nature", "🌿 VK Nature")])
    assert [s.soft_id for s in softs] == ["engine", "ch_1", "ch_5", "p_nature"]
    assert softs[0].kind == bot.SOFT_KIND_ENGINE
    assert softs[1].kind == bot.SOFT_KIND_CHANNEL and softs[1].channel_id == 1
    assert softs[3].kind == bot.SOFT_KIND_PROCESS


def test_build_soft_list_sorts_channels_by_id():
    channels = [SimpleNamespace(id=9, name="B", enabled=True),
                SimpleNamespace(id=2, name="A", enabled=True)]
    softs = bot.build_soft_list(channels, [])
    assert [s.soft_id for s in softs] == ["engine", "ch_2", "ch_9"]


def test_find_soft():
    softs = bot.build_soft_list(_channels(), [])
    assert bot.find_soft(softs, "ch_5").title == "📺 Кино"
    assert bot.find_soft(softs, "ghost") is None


def test_render_soft_list_uses_status_dots():
    softs = bot.build_soft_list(_channels(), [("p_shorts", "🎬 Shorts")])
    statuses = {"engine": "🟢", "ch_1": "🟢", "ch_5": "⚪", "p_shorts": "🔴"}
    text = bot.render_soft_list(softs, statuses)
    assert "🟢 📰 Движок новостей" in text
    assert "⚪ 📺 Кино" in text
    assert "🔴 🎬 Shorts" in text


def test_soft_list_rows_carry_dot_and_id():
    softs = bot.build_soft_list(_channels(), [])
    rows = {r.soft_id: r for r in bot.soft_list_rows(softs, {"ch_1": "🟢"})}
    assert rows["ch_1"].dot == "🟢"
    assert rows["ch_5"].dot == "❔"  # нет в statuses → дефолт


def test_genre_menu_carries_index_not_the_cyrillic_name():
    """callback_data ограничен 64 байтами, кириллица ест по два байта на букву."""
    from app import bot_keyboards as kb

    markup = kb.genre_menu("p_music", ["Фонк", "Рэп", "Атмосферный"])

    codes = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert codes[:3] == ["soft:genq:p_music:0", "soft:genq:p_music:1", "soft:genq:p_music:2"]
    assert all(len(code.encode()) <= 64 for code in codes)


def test_genre_menu_lays_buttons_two_per_row_and_ends_with_editor_and_back():
    from app import bot_keyboards as kb

    markup = kb.genre_menu("p_music", ["Фонк", "Рэп", "Поп"])

    assert [len(row) for row in markup.inline_keyboard] == [2, 1, 2]
    assert [b.callback_data for b in markup.inline_keyboard[-1]] == [
        "soft:gedit:p_music", "soft:open:p_music"
    ]


def test_genre_editor_lists_pairs_and_offers_add():
    """Правка списка жанров из бота (ТЗ 2026-08-18): config.yaml Музыки не
    версионируется, и правка руками на сервере теряется."""
    from app import bot_keyboards as kb

    markup = kb.genre_editor_menu("p_music", (("Фонк", "русский фонк"), ("Рэп", "рэп")))

    codes = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert codes[:2] == ["soft:gdel:p_music:0", "soft:gdel:p_music:1"]
    assert "soft:gadd:p_music" in codes
    assert all(len(code.encode()) <= 64 for code in codes)


def test_soundcloud_soft_gets_the_genre_button():
    from app import bot_keyboards as kb

    markup = kb.soft_menu("p_music", kind="process", running=True, soundcloud=True)

    codes = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "soft:gen:p_music" in codes


def test_soft_without_soundcloud_has_no_genre_button():
    from app import bot_keyboards as kb

    markup = kb.soft_menu("p_minus", kind="process", running=True, soundcloud=False)

    codes = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "soft:gen:p_minus" not in codes
