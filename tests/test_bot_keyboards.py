from types import SimpleNamespace

from app import bot_keyboards as kb
from app.manager.contract import SoftContract


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


def test_main_menu_is_only_softs():
    texts = [b.text for row in kb.main_menu().keyboard for b in row]
    assert texts == [kb.BTN_SOFTS]


def test_softs_list_menu_opens_each_soft():
    rows = [
        SimpleNamespace(soft_id="engine", title="Движок", dot="🟢"),
        SimpleNamespace(soft_id="ch_3", title="Кино", dot="⚪"),
    ]
    datas = _callback_datas(kb.softs_list_menu(rows))
    assert "soft:open:engine" in datas
    assert "soft:open:ch_3" in datas


def test_soft_menu_channel_delegates_to_ch_handlers():
    datas = _callback_datas(kb.soft_menu("ch_3", kind="channel", running=True, channel_id=3))
    assert "soft:off:ch_3" in datas  # включён → кнопка выключения
    assert "ch:set:3:maxposts" in datas  # лимит → готовый обработчик канала
    assert "ch:set:3:interval" in datas
    assert "ch:sources:3" in datas
    assert "ch:open:3" in datas  # доп. настройки → карточка канала
    assert "soft:list" in datas


def test_soft_menu_process_edits_contract():
    """У внешнего софта кнопки лимитов больше не заглушка — ведут в контракт."""
    datas = _callback_datas(kb.soft_menu("p_music", kind="process", running=False))
    assert "soft:on:p_music" in datas
    assert "soft:status:p_music" in datas
    assert "soft:lim:p_music:maxposts" in datas
    assert "soft:lim:p_music:interval" in datas
    assert "soft:lim:p_music:quiet" in datas
    assert "soft:cfg:p_music" in datas
    assert not any(d.startswith("soft:na:") for d in datas)


def test_soft_menu_shows_contract_values_on_buttons():
    contract = SoftContract(max_posts_per_day=24, min_interval_minutes=55,
                            max_interval_minutes=65, quiet_start_hour=0, quiet_end_hour=7)
    markup = kb.soft_menu("p_music", kind="process", running=True, contract=contract)
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert any("24" in x for x in labels)
    assert any("55–65" in x for x in labels)
    assert any("0–7" in x for x in labels)


def test_soft_menu_without_contract_says_not_set():
    labels = [b.text for row in kb.soft_menu("p_music", kind="process", running=True).inline_keyboard
              for b in row]
    assert any("не задан" in x for x in labels)
