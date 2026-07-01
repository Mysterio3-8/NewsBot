import pytest
from PySide6.QtWidgets import QApplication

from app.db.repository import Repository, init_db, make_engine
from app.ui.pages.sources_page import SourceListWidget, SourcesPage

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def make_repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    return Repository(engine)


def test_source_list_widget_refresh_populates_table(tmp_path):
    repo = make_repo(tmp_path)
    repo.create_source(type="tg", name="Новостной канал", url="https://t.me/x", priority=7)

    widget = SourceListWidget(source_type="tg")
    widget.bind_repository(repo)

    assert widget.table.rowCount() == 1
    assert widget.table.item(0, 0).text() == "Новостной канал"
    assert widget.table.item(0, 2).text() == "7"


def test_source_list_widget_filters_by_type(tmp_path):
    repo = make_repo(tmp_path)
    repo.create_source(type="tg", name="TG", url="https://t.me/x")
    repo.create_source(type="vk", name="VK", url="https://vk.com/x")

    widget = SourceListWidget(source_type="vk")
    widget.bind_repository(repo)

    assert widget.table.rowCount() == 1
    assert widget.table.item(0, 0).text() == "VK"


def test_sources_page_binds_both_tabs(tmp_path):
    repo = make_repo(tmp_path)
    repo.create_source(type="tg", name="TG", url="https://t.me/x")
    repo.create_source(type="vk", name="VK", url="https://vk.com/x")

    page = SourcesPage()
    page.bind_repository(repo)

    assert page.telegram_tab.table.rowCount() == 1
    assert page.vk_tab.table.rowCount() == 1


def test_delete_source_removes_row(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="TG", url="https://t.me/x")

    widget = SourceListWidget(source_type="tg")
    widget.bind_repository(repo)
    widget.table.selectRow(0)

    widget._on_delete()

    assert widget.table.rowCount() == 0
    assert repo.list_sources() == []
