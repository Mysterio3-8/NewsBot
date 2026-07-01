from unittest.mock import AsyncMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from app.core.publishing.footer import FooterLinks
from app.core.publishing.telegram_publisher import PublishResult, TelegramPublisher
from app.db.repository import Repository, init_db, make_engine
from app.ui.pages.publishing_page import PublishingPage


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def make_repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    return Repository(engine)


def test_refresh_populates_queued_posts(qapp, tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    repo.create_processed_post(
        raw_post_id=raw_post.id, score=88, headline="Заголовок", status="queued"
    )

    page = PublishingPage()
    page.bind(repo, AsyncMock(spec=TelegramPublisher), "@channel")

    assert page.table.rowCount() == 1
    assert page.table.item(0, 0).text() == "Заголовок"


def test_publish_selected_marks_published(qapp, tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    processed = repo.create_processed_post(
        raw_post_id=raw_post.id, score=88, rewritten_text="Текст", status="queued"
    )

    publisher = AsyncMock(spec=TelegramPublisher)
    publisher.publish.return_value = PublishResult(success=True, message_id=1, error=None)

    page = PublishingPage()
    page.bind(repo, publisher, "@channel")
    page.table.selectRow(0)

    with patch("app.ui.pages.publishing_page.QMessageBox.information"):
        page._on_publish()

    assert repo.get_processed_post(processed.id).status == "published"
    assert page.table.rowCount() == 0  # ушёл из очереди queued


def test_publish_passes_footer_links_through(qapp, tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    repo.create_processed_post(
        raw_post_id=raw_post.id, score=88, rewritten_text="Текст", status="queued"
    )

    publisher = AsyncMock(spec=TelegramPublisher)
    publisher.publish.return_value = PublishResult(success=True, message_id=1, error=None)
    footer_links = FooterLinks(label="Подписывайтесь на нас", telegram_url="https://t.me/x")

    page = PublishingPage()
    page.bind(repo, publisher, "@channel", footer_links)
    page.table.selectRow(0)

    with patch("app.ui.pages.publishing_page.QMessageBox.information"):
        page._on_publish()

    assert "Подписывайтесь на нас" in publisher.publish.call_args.kwargs["text"]
