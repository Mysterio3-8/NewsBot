import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest
from PySide6.QtWidgets import QApplication

from app.db.repository import Repository, init_db, make_engine
from app.ui.pages.home_page import HomePage


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def make_repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    return Repository(engine)


def test_refresh_counts_shows_queued_and_published(qapp, tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    raw_a = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="a")
    raw_b = repo.create_raw_post(source_id=source.id, external_id="2", raw_text="b")
    repo.create_processed_post(raw_post_id=raw_a.id, score=90, status="queued")
    published = repo.create_processed_post(raw_post_id=raw_b.id, score=80, status="queued")
    repo.update_processed_post_status(
        published.id, "published", published_at=datetime.datetime.now(datetime.timezone.utc)
    )

    page = HomePage()
    page.bind_repository(repo)

    assert page.new_posts_label.text() == "Новых постов: 1"
    assert page.published_today_label.text() == "Опубликовано сегодня: 1"


def test_start_stop_buttons_toggle_status(qapp):
    page = HomePage()

    page.start_button.click()
    assert page.status_label.text() == "Статус: работает"
    assert page.stop_button.isEnabled() is True
    assert page.start_button.isEnabled() is False

    page.stop_button.click()
    assert page.status_label.text() == "Статус: остановлено"
    assert page.start_button.isEnabled() is True


def test_check_now_button_calls_bound_callback_and_refreshes(qapp, tmp_path):
    repo = make_repo(tmp_path)
    page = HomePage()
    page.bind_repository(repo)
    callback = AsyncMock()
    page.bind_check_now(callback)

    page.check_now_button.click()

    callback.assert_awaited_once()
    assert "Последняя проверка: —" != page.last_check_label.text()


def test_check_now_button_warns_when_not_bound(qapp):
    page = HomePage()

    with patch("app.ui.pages.home_page.QMessageBox.warning") as mock_warning:
        page.check_now_button.click()

    mock_warning.assert_called_once()


def test_check_now_button_shows_warning_on_error(qapp, tmp_path):
    repo = make_repo(tmp_path)
    page = HomePage()
    page.bind_repository(repo)
    page.bind_check_now(AsyncMock(side_effect=RuntimeError("сбой сети")))

    with patch("app.ui.pages.home_page.QMessageBox.warning") as mock_warning:
        page.check_now_button.click()

    mock_warning.assert_called_once()
    assert page.check_now_button.isEnabled() is True


def test_start_stop_buttons_call_bound_scheduler_controls(qapp):
    page = HomePage()
    on_start = Mock()
    on_stop = Mock()
    page.bind_scheduler_controls(on_start=on_start, on_stop=on_stop)

    page.start_button.click()
    on_start.assert_called_once()

    page.stop_button.click()
    on_stop.assert_called_once()
