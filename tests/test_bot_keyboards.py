from types import SimpleNamespace

from app import bot_keyboards as kb


def _callback_datas(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


def test_main_menu_has_all_sections():
    texts = [b.text for row in kb.main_menu().keyboard for b in row]
    assert set(texts) == set(kb.MAIN_MENU_BUTTONS)


def test_autoposting_menu_shows_stop_when_running():
    datas = _callback_datas(kb.autoposting_menu(running=True))
    assert "auto:stop" in datas
    assert "auto:run" not in datas


def test_autoposting_menu_shows_run_when_stopped():
    datas = _callback_datas(kb.autoposting_menu(running=False))
    assert "auto:run" in datas
    assert "auto:stop" not in datas


def test_settings_menu_puts_values_on_buttons():
    markup = kb.settings_menu(
        interval=2, freshness=12, maxposts=999, provider="groq", photo_design=True
    )
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert any("2 мин" in label for label in labels)
    assert any("12 ч" in label for label in labels)
    assert any("999" in label for label in labels)
    assert any("groq" in label for label in labels)
    assert any("Оформление фото" in label and "вкл" in label for label in labels)


def test_settings_menu_shows_photo_design_off():
    markup = kb.settings_menu(
        interval=2, freshness=12, maxposts=999, provider="groq", photo_design=False
    )
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert any("Оформление фото" in label and "выкл" in label for label in labels)


def test_sources_menu_toggle_callback_per_source():
    sources = [
        SimpleNamespace(id=1, type="tg", name="A", enabled=True),
        SimpleNamespace(id=2, type="vk", name="B", enabled=False),
    ]
    datas = _callback_datas(kb.sources_menu(sources))
    assert "src:toggle:1" in datas
    assert "src:toggle:2" in datas
    assert "src:add" in datas


def test_provider_menu_marks_current():
    markup = kb.provider_menu(["groq", "ollama"], current="groq")
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert any(label.startswith("✅") and "groq" in label for label in labels)


def test_process_menu_uses_prefix():
    datas = _callback_datas(kb.process_menu("nature"))
    assert "nature:run" in datas
    assert "nature:stop" in datas
    assert "nature:status" in datas
