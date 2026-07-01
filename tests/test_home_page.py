import datetime

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
