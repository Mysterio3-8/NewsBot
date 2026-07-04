from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.monitoring.telegram_fetcher import TelegramFetcher, message_to_post


def make_message(**overrides):
    defaults = dict(
        id=42,
        message="Текст поста",
        date=datetime.now(timezone.utc),
        views=1000,
        media=None,
        poll=None,
        pinned=False,
        action=None,
        photo=None,
        video=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_message_to_post_maps_regular_text_message():
    message = make_message()

    post = message_to_post(message)

    assert post.external_id == "42"
    assert post.text == "Текст поста"
    assert post.post_type == "text"
    assert post.views == 1000
    assert post.has_media is False
    assert post.media_urls == []


def test_message_to_post_passes_through_downloaded_media_paths():
    message = make_message(media=object(), photo=object())
    post = message_to_post(message, media_paths=["output/tg_raw_media/1.jpg"])
    assert post.media_urls == ["output/tg_raw_media/1.jpg"]


def test_message_to_post_passes_through_downloaded_video_path():
    message = make_message(media=object(), video=object())
    post = message_to_post(message, video_path="output/tg_raw_media/1.mp4")
    assert post.video_path == "output/tg_raw_media/1.mp4"


def test_message_to_post_defaults_video_path_to_none():
    message = make_message()
    assert message_to_post(message).video_path is None


def test_message_to_post_detects_media():
    message = make_message(media=object())

    post = message_to_post(message)

    assert post.has_media is True


def test_message_to_post_detects_poll():
    message = make_message(poll=object())
    assert message_to_post(message).post_type == "poll"


def test_message_to_post_detects_pinned():
    message = make_message(pinned=True)
    assert message_to_post(message).post_type == "pinned"


def test_message_to_post_detects_service_message():
    message = make_message(action=object())
    assert message_to_post(message).post_type == "service"


def test_message_to_post_defaults_views_to_zero_when_none():
    message = make_message(views=None)
    assert message_to_post(message).views == 0


@pytest.mark.asyncio
async def test_download_photo_returns_empty_when_no_photo():
    fetcher = TelegramFetcher(api_id=1, api_hash="hash", session_name="test")
    fetcher._client = MagicMock()
    fetcher._client.download_media = AsyncMock()

    result = await fetcher._download_photo(make_message(photo=None))

    assert result == []
    fetcher._client.download_media.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_photo_downloads_and_returns_path_when_photo_present():
    fetcher = TelegramFetcher(api_id=1, api_hash="hash", session_name="test")
    fetcher._client = MagicMock()
    fetcher._client.download_media = AsyncMock(return_value="output/tg_raw_media/42.jpg")
    message = make_message(photo=object())

    result = await fetcher._download_photo(message)

    assert result == ["output/tg_raw_media/42.jpg"]
    fetcher._client.download_media.assert_awaited_once()


@pytest.mark.asyncio
async def test_download_photo_returns_empty_on_download_error():
    fetcher = TelegramFetcher(api_id=1, api_hash="hash", session_name="test")
    fetcher._client = MagicMock()
    fetcher._client.download_media = AsyncMock(side_effect=Exception("сбой сети"))
    message = make_message(photo=object())

    result = await fetcher._download_photo(message)

    assert result == []


@pytest.mark.asyncio
async def test_download_video_returns_none_when_no_video():
    fetcher = TelegramFetcher(api_id=1, api_hash="hash", session_name="test")
    fetcher._client = MagicMock()
    fetcher._client.download_media = AsyncMock()

    result = await fetcher._download_video(make_message(video=None))

    assert result is None
    fetcher._client.download_media.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_video_downloads_and_returns_path_when_video_present():
    fetcher = TelegramFetcher(api_id=1, api_hash="hash", session_name="test")
    fetcher._client = MagicMock()
    fetcher._client.download_media = AsyncMock(return_value="output/tg_raw_media/42.mp4")
    message = make_message(video=object())

    result = await fetcher._download_video(message)

    assert result == "output/tg_raw_media/42.mp4"
    fetcher._client.download_media.assert_awaited_once()


@pytest.mark.asyncio
async def test_download_video_returns_none_on_download_error():
    fetcher = TelegramFetcher(api_id=1, api_hash="hash", session_name="test")
    fetcher._client = MagicMock()
    fetcher._client.download_media = AsyncMock(side_effect=Exception("сбой сети"))
    message = make_message(video=object())

    result = await fetcher._download_video(message)

    assert result is None


@pytest.mark.asyncio
async def test_fetch_recent_posts_stops_at_cutoff():
    now = datetime.now(timezone.utc)
    messages = [
        make_message(id=3, date=now),
        make_message(id=2, date=now - timedelta(hours=1)),
        make_message(id=1, date=now - timedelta(hours=100)),  # старше cutoff
    ]

    async def fake_iter_messages(*args, **kwargs):
        for message in messages:
            yield message

    fetcher = TelegramFetcher(api_id=1, api_hash="hash", session_name="test")
    fetcher._client = MagicMock()
    fetcher._client.iter_messages = fake_iter_messages
    fetcher._client.__aenter__ = AsyncMock(return_value=fetcher._client)
    fetcher._client.__aexit__ = AsyncMock(return_value=False)

    posts = await fetcher.fetch_recent_posts("https://t.me/test", max_age_hours=24)

    assert [p.external_id for p in posts] == ["3", "2"]


@pytest.mark.asyncio
async def test_fetch_recent_posts_skips_download_for_known_external_ids():
    """Регрессия: без этого фильтра каждый цикл проверки (каждые 10 мин, окно до
    7 дней) заново скачивал медиа уже известных сообщений — на проде один файл
    скачался 245 раз и забил диск на 100% прежде чем нашли причину."""
    now = datetime.now(timezone.utc)
    messages = [
        make_message(id=3, date=now, photo=object()),
        make_message(id=2, date=now, photo=object()),  # уже известен
    ]

    async def fake_iter_messages(*args, **kwargs):
        for message in messages:
            yield message

    fetcher = TelegramFetcher(api_id=1, api_hash="hash", session_name="test")
    fetcher._client = MagicMock()
    fetcher._client.iter_messages = fake_iter_messages
    fetcher._client.download_media = AsyncMock(return_value="output/tg_raw_media/x.jpg")
    fetcher._client.__aenter__ = AsyncMock(return_value=fetcher._client)
    fetcher._client.__aexit__ = AsyncMock(return_value=False)

    posts = await fetcher.fetch_recent_posts(
        "https://t.me/test", max_age_hours=24, known_external_ids={"2"}
    )

    assert [p.external_id for p in posts] == ["3"]
    fetcher._client.download_media.assert_awaited_once()  # только для сообщения id=3
