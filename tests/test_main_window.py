import shutil

import pytest
from PySide6.QtWidgets import QApplication

from app.config.loader import CONFIG_PATH, load_config
from app.db.repository import Repository, init_db, make_engine
from app.ui.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def config_copy(tmp_path):
    dest = tmp_path / "config.yaml"
    shutil.copy(CONFIG_PATH, dest)
    return dest


def make_repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    return Repository(engine)


def test_main_window_constructs_and_binds_pages_without_error(qapp, tmp_path, config_copy, monkeypatch):
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    config = load_config(config_copy)
    repo = make_repo(tmp_path)

    window = MainWindow(config=config, config_path=config_copy, repo=repo)

    assert window.pages_stack.count() == 8
    assert window.menu_list.count() == 8


def test_main_window_publishing_page_stays_unbound_without_bot_token(
    qapp, tmp_path, config_copy, monkeypatch
):
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    config = load_config(config_copy)
    repo = make_repo(tmp_path)

    window = MainWindow(config=config, config_path=config_copy, repo=repo)

    assert window.publishing_page._repo is None
