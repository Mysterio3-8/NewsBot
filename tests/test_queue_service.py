from pathlib import Path
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
async def test_publish_queued_post_marks_published_on_success(tmp_path, caplog):
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

    with caplog.at_level("INFO", logger="publishing"):
        result = await publish_queued_post(
            repo, publisher, post_id=processed.id, chat_id="@channel"
        )

    assert result.success is True
    publisher.publish.assert_awaited_once_with(
        chat_id="@channel", text="Текст новости", image_paths=[], parse_mode="HTML"
    )
    assert repo.list_processed_posts(status="published")[0].id == processed.id
    assert repo.get_published_network_at(processed.id, "tg") is not None
    assert repo.get_published_network_at(processed.id, "vk") is None
    assert "опубликован в TG" in caplog.text


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
    footer_links = FooterLinks(telegram_url="https://t.me/x")

    await publish_queued_post(
        repo, publisher, post_id=processed.id, chat_id="@channel", footer_links=footer_links
    )

    _, kwargs = publisher.publish.call_args
    assert kwargs["text"] == (
        "Рост &amp; падение\n\n"
        '<a href="https://t.me/x">Новости в трёх словах</a>'
    )


@pytest.mark.asyncio
async def test_publish_queued_post_moves_hashtags_after_footer(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    processed = repo.create_processed_post(
        raw_post_id=raw_post.id,
        score=90,
        headline="Заголовок",
        rewritten_text="Текст новости.\n\n#технологии #apple",
        status="queued",
    )

    publisher = AsyncMock(spec=TelegramPublisher)
    publisher.publish.return_value = PublishResult(success=True, message_id=1, error=None)
    footer_links = FooterLinks(telegram_url="https://t.me/x")

    await publish_queued_post(
        repo,
        publisher,
        post_id=processed.id,
        chat_id="@channel",
        footer_links=footer_links,
        include_hashtags=True,
    )

    _, kwargs = publisher.publish.call_args
    assert kwargs["text"] == (
        "Текст новости.\n\n"
        '<a href="https://t.me/x">Новости в трёх словах</a>\n\n'
        "#технологии #apple"
    )


@pytest.mark.asyncio
async def test_publish_queued_post_excludes_hashtags_by_default(tmp_path):
    """По умолчанию хэштеги не публикуются (пользователь отключил 2026-07-03) —
    include_hashtags=True нужно передать явно, чтобы их вернуть."""
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    processed = repo.create_processed_post(
        raw_post_id=raw_post.id,
        score=90,
        headline="Заголовок",
        rewritten_text="Текст новости.\n\n#технологии #apple",
        status="queued",
    )

    publisher = AsyncMock(spec=TelegramPublisher)
    publisher.publish.return_value = PublishResult(success=True, message_id=1, error=None)

    await publish_queued_post(repo, publisher, post_id=processed.id, chat_id="@channel")

    _, kwargs = publisher.publish.call_args
    assert "#технологии" not in kwargs["text"]
    assert kwargs["text"] == "Текст новости."


@pytest.mark.asyncio
async def test_publish_queued_post_converts_markdown_bold_italic_to_html(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    processed = repo.create_processed_post(
        raw_post_id=raw_post.id,
        score=90,
        rewritten_text="**Путин** заявил, что *ситуация стабильна*.",
        status="queued",
    )

    publisher = AsyncMock(spec=TelegramPublisher)
    publisher.publish.return_value = PublishResult(success=True, message_id=1, error=None)

    await publish_queued_post(repo, publisher, post_id=processed.id, chat_id="@channel")

    _, kwargs = publisher.publish.call_args
    assert kwargs["text"] == "<b>Путин</b> заявил, что <i>ситуация стабильна</i>."


@pytest.mark.asyncio
async def test_publish_queued_post_passes_image_paths_from_db(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    processed = repo.create_processed_post(
        raw_post_id=raw_post.id,
        score=90,
        rewritten_text="Текст",
        image_paths=["output/images/1/a.jpg", "output/images/1/b.jpg"],
        status="queued",
    )

    publisher = AsyncMock(spec=TelegramPublisher)
    publisher.publish.return_value = PublishResult(success=True, message_id=1, error=None)

    await publish_queued_post(repo, publisher, post_id=processed.id, chat_id="@channel")

    _, kwargs = publisher.publish.call_args
    assert kwargs["image_paths"] == [
        Path("output/images/1/a.jpg"),
        Path("output/images/1/b.jpg"),
    ]


@pytest.mark.asyncio
async def test_publish_queued_post_passes_video_path_from_db(tmp_path):
    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    processed = repo.create_processed_post(
        raw_post_id=raw_post.id,
        score=90,
        rewritten_text="Текст",
        video_path="output/videos/1/a.mp4",
        status="queued",
    )

    publisher = AsyncMock(spec=TelegramPublisher)
    publisher.publish.return_value = PublishResult(success=True, message_id=1, error=None)

    await publish_queued_post(repo, publisher, post_id=processed.id, chat_id="@channel")

    _, kwargs = publisher.publish.call_args
    assert kwargs["video_path"] == Path("output/videos/1/a.mp4")


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


@pytest.mark.asyncio
async def test_publish_queued_post_blocked_by_daily_cap_stays_queued(tmp_path):
    """Антиспам-стопор: при достигнутом дневном лимите публикация НЕ уходит,
    publisher не вызывается, а пост остаётся queued (не теряется, не failed)."""
    import datetime

    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    now = datetime.datetime.utcnow()
    for i in range(6):
        raw = repo.create_raw_post(source_id=source.id, external_id=f"old{i}", raw_text="x")
        p = repo.create_processed_post(raw_post_id=raw.id, score=90, status="queued")
        repo.update_processed_post_status(p.id, "published", published_at=now)

    raw_new = repo.create_raw_post(source_id=source.id, external_id="new", raw_text="новость")
    new_post = repo.create_processed_post(
        raw_post_id=raw_new.id, score=90, rewritten_text="Текст", status="queued"
    )

    publisher = AsyncMock(spec=TelegramPublisher)
    result = await publish_queued_post(
        repo, publisher, post_id=new_post.id, chat_id="@channel", max_posts_per_day=6
    )

    assert result.success is False
    assert "throttled" in result.error
    publisher.publish.assert_not_awaited()
    assert repo.get_processed_post(new_post.id).status == "queued"


@pytest.mark.asyncio
async def test_publish_queued_post_blocked_by_min_interval(tmp_path):
    import datetime

    repo = make_repo(tmp_path)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    now = datetime.datetime.utcnow()
    raw_recent = repo.create_raw_post(source_id=source.id, external_id="recent", raw_text="x")
    recent = repo.create_processed_post(raw_post_id=raw_recent.id, score=90, status="queued")
    repo.update_processed_post_status(
        recent.id, "published", published_at=now - datetime.timedelta(minutes=10)
    )

    raw_new = repo.create_raw_post(source_id=source.id, external_id="new", raw_text="новость")
    new_post = repo.create_processed_post(
        raw_post_id=raw_new.id, score=90, rewritten_text="Текст", status="queued"
    )

    publisher = AsyncMock(spec=TelegramPublisher)
    result = await publish_queued_post(
        repo, publisher, post_id=new_post.id, chat_id="@channel", min_interval_minutes=180
    )

    assert result.success is False
    assert "throttled" in result.error
    publisher.publish.assert_not_awaited()
