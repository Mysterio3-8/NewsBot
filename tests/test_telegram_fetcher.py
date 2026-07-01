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
