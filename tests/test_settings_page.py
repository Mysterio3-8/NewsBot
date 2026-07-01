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

    assert page.filters_tab.min_score_input.value() == 75
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


def test_read_only_tabs_show_current_values(qapp, config_copy):
    config = load_config(config_copy)
    page = SettingsPage()

    page.bind_config(config, config_copy)

    assert page.ai_tab._layout.rowCount() == 4
    assert page.publishing_tab._layout.rowCount() == 4
