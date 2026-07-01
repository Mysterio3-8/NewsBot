from unittest.mock import AsyncMock

import pytest

from app.core.publishing.footer import FooterLinks
from app.core.publishing.queue_service import PostNotFoundError, publish_queued_post
from app.core.publishing.telegram_publisher import PublishResult, TelegramPublisher
from app.db.repository import Repository, init_db, make_engine


def make_repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    return Repository(engine)


@pytest.mark.asyncio
async def test_publish_queued_post_marks_published_on_success(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    processed = repo.create_processed_post(
        raw_post_id=raw_post.id,
        score=90,
        headline="Заголовок",
        rewritten_text="Текст новости",
        status="queued",
    )

    publisher = AsyncMock(spec=TelegramPublisher)
    publisher.publish.return_value = PublishResult(success=True, message_id=1, error=None)

    result = await publish_queued_post(
        repo, publisher, post_id=processed.id, chat_id="@channel"
    )

    assert result.success is True
    publisher.publish.assert_awaited_once_with(
        chat_id="@channel", text="Заголовок\n\nТекст новости", parse_mode="HTML"
    )
    assert repo.list_processed_posts(status="published")[0].id == processed.id


@pytest.mark.asyncio
async def test_publish_queued_post_escapes_html_and_appends_footer(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    processed = repo.create_processed_post(
        raw_post_id=raw_post.id,
        score=90,
        headline="Цены <ниже>",
        rewritten_text="Рост & падение",
        status="queued",
    )

    publisher = AsyncMock(spec=TelegramPublisher)
    publisher.publish.return_value = PublishResult(success=True, message_id=1, error=None)
    footer_links = FooterLinks(label="Подписывайтесь на нас", telegram_url="https://t.me/x")

    await publish_queued_post(
        repo, publisher, post_id=processed.id, chat_id="@channel", footer_links=footer_links
    )

    _, kwargs = publisher.publish.call_args
    assert kwargs["text"] == (
        "Цены &lt;ниже&gt;\n\n"
        "Рост &amp; падение\n\n"
        'Подписывайтесь на нас: <a href="https://t.me/x">Telegram</a>'
    )


@pytest.mark.asyncio
async def test_publish_queued_post_marks_failed_on_error(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    processed = repo.create_processed_post(
        raw_post_id=raw_post.id, score=90, rewritten_text="Текст", status="queued"
    )

    publisher = AsyncMock(spec=TelegramPublisher)
    publisher.publish.return_value = PublishResult(success=False, message_id=None, error="сбой")

    await publish_queued_post(repo, publisher, post_id=processed.id, chat_id="@channel")

    assert repo.list_processed_posts(status="failed")[0].id == processed.id


@pytest.mark.asyncio
async def test_publish_queued_post_raises_when_missing(tmp_path):
    repo = make_repo(tmp_path)
    publisher = AsyncMock(spec=TelegramPublisher)

    with pytest.raises(PostNotFoundError):
        await publish_queued_post(repo, publisher, post_id=999, chat_id="@channel")
