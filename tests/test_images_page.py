import shutil
from unittest.mock import patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.config.loader import CONFIG_PATH, load_config
from app.ui.pages.images_page import ImagesPage


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def config_copy(tmp_path):
    dest = tmp_path / "config.yaml"
    shutil.copy(CONFIG_PATH, dest)
    return dest


def test_bind_config_checks_configured_providers_in_order(qapp, config_copy):
    config = load_config(config_copy)
    page = ImagesPage()

    page.bind_config(config, config_copy)

    checked_names = [
        page.provider_list.item(i).text()
        for i in range(page.provider_list.count())
        if page.provider_list.item(i).checkState() == Qt.CheckState.Checked
    ]
    assert checked_names == config.images.providers_order


def test_move_up_reorders_provider_list(qapp, config_copy):
    config = load_config(config_copy)
    page = ImagesPage()
    page.bind_config(config, config_copy)

    page.provider_list.setCurrentRow(1)
    second_item_text = page.provider_list.item(1).text()
    page._move_selected(-1)

    assert page.provider_list.item(0).text() == second_item_text


def test_save_persists_provider_order_and_watermark_settings(qapp, config_copy):
    config = load_config(config_copy)
    page = ImagesPage()
    page.bind_config(config, config_copy)

    page.opacity_input.setValue(50)
    page.count_per_post_input.setValue(2)
    page.size_ratio_input.setValue(15)

    with patch("app.ui.pages.images_page.QMessageBox.information"):
        page._on_save()

    reloaded = load_config(config_copy)
    assert reloaded.watermark.opacity == 50
    assert reloaded.images.count_per_post == 2
    assert reloaded.watermark.size_ratio == pytest.approx(0.15)


def test_bind_config_shows_size_ratio_as_percent(qapp, config_copy):
    config = load_config(config_copy)
    page = ImagesPage()

    page.bind_config(config, config_copy)

    assert page.size_ratio_input.value() == round(config.watermark.size_ratio * 100)
