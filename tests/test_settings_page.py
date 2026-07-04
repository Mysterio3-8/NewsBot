import shutil
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication

from app.config.loader import CONFIG_PATH, ConfigValidationError, load_config, update_config_section
from app.ui.pages.settings_page import SettingsPage, parse_lines


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def config_copy(tmp_path):
    dest = tmp_path / "config.yaml"
    shutil.copy(CONFIG_PATH, dest)
    return dest


def test_parse_lines_strips_and_skips_blank():
    assert parse_lines("скидка\n\nреклама\n  \nпромокод") == ["скидка", "реклама", "промокод"]


def test_bind_config_populates_filters_tab(qapp, config_copy):
    update_config_section(config_copy, "filters", stop_words=["скидка"])
    config = load_config(config_copy)
    page = SettingsPage()

    page.bind_config(config, config_copy)

    assert page.filters_tab.min_score_input.value() == 55
    assert "скидка" in page.filters_tab.stop_words_input.toPlainText()


def test_save_filters_persists_and_reloads(qapp, config_copy):
    config = load_config(config_copy)
    page = SettingsPage()
    page.bind_config(config, config_copy)

    page.filters_tab.min_score_input.setValue(60)
    page.filters_tab.whitelist_input.setPlainText("Кремль\nПрезидент")

    with patch("app.ui.pages.settings_page.QMessageBox.information"):
        page.filters_tab._on_save()

    reloaded = load_config(config_copy)
    assert reloaded.filters.min_score == 60
    assert reloaded.filters.whitelist_keywords == ["Кремль", "Президент"]


def test_save_filters_shows_warning_when_config_validation_fails(qapp, config_copy):
    config = load_config(config_copy)
    page = SettingsPage()
    page.bind_config(config, config_copy)

    with (
        patch(
            "app.ui.pages.settings_page.update_config_section",
            side_effect=ConfigValidationError("min_score вне диапазона"),
        ),
        patch("app.ui.pages.settings_page.QMessageBox.warning") as mock_warning,
    ):
        page.filters_tab._on_save()

    mock_warning.assert_called_once()


def test_general_tab_populates_and_saves(qapp, config_copy):
    config = load_config(config_copy)
    page = SettingsPage()
    page.bind_config(config, config_copy)

    assert page.general_tab.name_input.text() == "AI News Rewriter"

    page.general_tab.name_input.setText("My News Bot")
    with patch("app.ui.pages.settings_page.QMessageBox.information"):
        page.general_tab._on_save()

    assert load_config(config_copy).app.name == "My News Bot"


def test_ai_tab_populates_and_saves(qapp, config_copy):
    config = load_config(config_copy)
    page = SettingsPage()
    page.bind_config(config, config_copy)

    assert page.ai_tab.provider_input.currentText() == "groq"
    assert page.ai_tab.model_input.text() == "llama-3.1-8b-instant"
    assert page.ai_tab.api_key_env_input.text() == "GROQ_API_KEY"

    page.ai_tab.model_input.setText("meta-llama/llama-3.3-70b-instruct:free")
    page.ai_tab.provider_input.setCurrentText("openrouter")
    page.ai_tab.temperature_input.setValue(0.5)
    with patch("app.ui.pages.settings_page.QMessageBox.information"):
        page.ai_tab._on_save()

    reloaded = load_config(config_copy)
    assert reloaded.llm.model == "meta-llama/llama-3.3-70b-instruct:free"
    assert reloaded.llm.provider == "openrouter"
    assert reloaded.llm.temperature == 0.5


def test_publishing_tab_populates_and_saves(qapp, config_copy):
    config = load_config(config_copy)
    page = SettingsPage()
    page.bind_config(config, config_copy)

    assert page.publishing_tab.telegram_chat_id_input.text() == "@NewsThreeWord"
    assert page.publishing_tab.vk_group_id_input.value() == 233689032

    page.publishing_tab.telegram_chat_id_input.setText("@OtherChannel")
    page.publishing_tab.vk_group_id_input.setValue(111)
    with patch("app.ui.pages.settings_page.QMessageBox.information"):
        page.publishing_tab._on_save()

    reloaded = load_config(config_copy)
    assert reloaded.publishing.telegram.destination == "@OtherChannel"
    assert reloaded.publishing.vk.destination == "111"
    assert reloaded.publishing.telegram.token_env == "TG_BOT_TOKEN"  # сохранился неизменным


def test_logs_tab_populates_and_saves(qapp, config_copy):
    config = load_config(config_copy)
    page = SettingsPage()
    page.bind_config(config, config_copy)

    assert page.logs_tab.level_input.currentText() == "INFO"

    page.logs_tab.level_input.setCurrentText("DEBUG")
    with patch("app.ui.pages.settings_page.QMessageBox.information"):
        page.logs_tab._on_save()

    assert load_config(config_copy).logging.level == "DEBUG"
