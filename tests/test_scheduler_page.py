import shutil
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication

from app.config.loader import CONFIG_PATH, load_config
from app.ui.pages.scheduler_page import SchedulerPage, parse_slots


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def config_copy(tmp_path):
    dest = tmp_path / "config.yaml"
    shutil.copy(CONFIG_PATH, dest)
    return dest


def test_parse_slots_accepts_valid_times():
    assert parse_slots("08:00\n09:20\n\n23:59") == ["08:00", "09:20", "23:59"]


def test_parse_slots_rejects_invalid_format():
    with pytest.raises(ValueError):
        parse_slots("25:00")


def test_parse_slots_rejects_garbage():
    with pytest.raises(ValueError):
        parse_slots("не время")


def test_bind_config_populates_fields(qapp, config_copy):
    config = load_config(config_copy)
    page = SchedulerPage()

    page.bind_config(config, config_copy)

    assert page.mode_input.currentText() == "fixed_slots"
    assert page.max_posts_per_day_input.value() == 12
    assert page.important_score_threshold_input.value() == 65
    assert page.min_interval_minutes_input.value() == 110
    assert page.jitter_minutes_input.value() == 2
    assert page.include_hashtags_input.isChecked() is False
    assert "08:00" in page.fixed_slots_input.toPlainText()


def test_save_persists_schedule_and_threshold(qapp, config_copy):
    config = load_config(config_copy)
    page = SchedulerPage()
    page.bind_config(config, config_copy)

    page.fixed_slots_input.setPlainText("07:00\n12:00\n19:00")
    page.max_posts_per_day_input.setValue(3)
    page.important_score_threshold_input.setValue(90)
    page.min_interval_minutes_input.setValue(200)
    page.jitter_minutes_input.setValue(20)
    page.include_hashtags_input.setChecked(True)

    with patch("app.ui.pages.scheduler_page.QMessageBox.information"):
        page._on_save()

    reloaded = load_config(config_copy)
    assert reloaded.publishing.schedule.fixed_slots == ["07:00", "12:00", "19:00"]
    assert reloaded.publishing.schedule.max_posts_per_day == 3
    assert reloaded.publishing.schedule.min_interval_minutes == 200
    assert reloaded.publishing.schedule.jitter_minutes == 20
    assert reloaded.filters.important_score_threshold == 90
    assert reloaded.rewrite.include_hashtags is True
    # targets должны остаться нетронутыми
    assert reloaded.publishing.telegram.destination == config.publishing.telegram.destination


def test_save_shows_warning_on_invalid_slot_format(qapp, config_copy):
    config = load_config(config_copy)
    page = SchedulerPage()
    page.bind_config(config, config_copy)

    page.fixed_slots_input.setPlainText("не время")

    with patch("app.ui.pages.scheduler_page.QMessageBox.warning") as mock_warning:
        page._on_save()

    mock_warning.assert_called_once()
