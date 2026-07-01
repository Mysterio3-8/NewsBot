from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.publishing.telegram_publisher import TelegramPublisher, split_caption

FAKE_TOKEN = "123456789:AAFakeTokenForTests1234567890abcdefghi"


def test_split_caption_returns_full_text_when_under_limit():
    caption, extra = split_caption("короткий текст", limit=1024)
    assert caption == "короткий текст"
    assert extra is None


def test_split_caption_splits_when_over_limit():
    text = "a" * 1500
    caption, extra = split_caption(text, limit=1024)
    assert len(caption) == 1024
    assert extra == "a" * 476


@pytest.mark.asyncio
async def test_publish_text_only_sends_message():
    publisher = TelegramPublisher(FAKE_TOKEN)
    publisher._bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=1))

    result = await publisher.publish(chat_id="@channel", text="новость")

    assert result.success is True
    assert result.message_id == 1
    publisher._bot.send_message.assert_awaited_once_with("@channel", "новость", parse_mode=None)


@pytest.mark.asyncio
async def test_publish_passes_parse_mode_through():
    publisher = TelegramPublisher(FAKE_TOKEN)
    publisher._bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=1))

    await publisher.publish(chat_id="@channel", text="новость", parse_mode="HTML")

    publisher._bot.send_message.assert_awaited_once_with("@channel", "новость", parse_mode="HTML")


@pytest.mark.asyncio
async def test_publish_with_single_image_sends_photo():
    publisher = TelegramPublisher(FAKE_TOKEN)
    publisher._bot.send_photo = AsyncMock(return_value=SimpleNamespace(message_id=2))

    result = await publisher.publish(
        chat_id="@channel", text="подпись", image_paths=["fake.jpg"]
    )

    assert result.success is True
    assert result.message_id == 2


@pytest.mark.asyncio
async def test_publish_retries_and_eventually_fails():
    publisher = TelegramPublisher(FAKE_TOKEN)
    publisher._bot.send_message = AsyncMock(side_effect=Exception("сеть недоступна"))

    with patch("app.core.publishing.telegram_publisher.asyncio.sleep", new=AsyncMock()):
        result = await publisher.publish(chat_id="@channel", text="новость")

    assert result.success is False
    assert "сеть недоступна" in result.error
    assert publisher._bot.send_message.await_count == 4


@pytest.mark.asyncio
async def test_publish_succeeds_after_transient_failure():
    publisher = TelegramPublisher(FAKE_TOKEN)
    publisher._bot.send_message = AsyncMock(
        side_effect=[Exception("временная ошибка"), SimpleNamespace(message_id=3)]
    )

    with patch("app.core.publishing.telegram_publisher.asyncio.sleep", new=AsyncMock()):
        result = await publisher.publish(chat_id="@channel", text="новость")

    assert result.success is True
    assert result.message_id == 3
