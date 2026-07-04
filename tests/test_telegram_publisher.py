from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.publishing.telegram_publisher import (
    BOT_API_FILE_LIMIT_BYTES,
    TelegramPublisher,
    split_caption,
)

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
async def test_publish_text_only_over_caption_limit_sends_once_not_split():
    """Регрессия: split_caption (лимит 1024, для фото-caption) применялся и к
    обычным текстовым сообщениям (реальный лимит 4096) — "хвост" улетал вторым
    сообщением и мог обрезать HTML-тег посередине (найдено 2026-07-02 на реальной
    публикации, footer-ссылка обрезалась и Telegram отвечал 'Unexpected end tag').
    """
    publisher = TelegramPublisher(FAKE_TOKEN)
    publisher._bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=1))
    long_text = "текст " * 200  # > 1024 символов, но < 4096 (лимит обычных сообщений)
    assert len(long_text) > 1024

    result = await publisher.publish(chat_id="@channel", text=long_text)

    assert result.success is True
    publisher._bot.send_message.assert_awaited_once_with("@channel", long_text, parse_mode=None)


@pytest.mark.asyncio
async def test_publish_with_single_image_sends_photo(tmp_path):
    publisher = TelegramPublisher(FAKE_TOKEN)
    publisher._bot.send_photo = AsyncMock(return_value=SimpleNamespace(message_id=2))
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"fake image bytes")

    result = await publisher.publish(
        chat_id="@channel", text="подпись", image_paths=[image_path]
    )

    assert result.success is True
    publisher._bot.send_photo.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_falls_back_to_text_when_image_missing_on_disk(tmp_path):
    """Регрессия: image_paths, скопированные с другой машины (напр. перенос БД на
    VPS), могут не существовать на диске — публикация должна уйти текстом, а не
    падать 4 попытки на заведомо безнадёжный sendPhoto (найдено 2026-07-04)."""
    publisher = TelegramPublisher(FAKE_TOKEN)
    publisher._bot.send_photo = AsyncMock()
    publisher._bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=3))
    missing_path = tmp_path / "does_not_exist.jpg"

    result = await publisher.publish(
        chat_id="@channel", text="подпись", image_paths=[missing_path]
    )

    assert result.success is True
    publisher._bot.send_photo.assert_not_awaited()
    publisher._bot.send_message.assert_awaited_once_with("@channel", "подпись", parse_mode=None)


@pytest.mark.asyncio
async def test_publish_falls_back_to_text_when_video_missing_on_disk(tmp_path):
    publisher = TelegramPublisher(FAKE_TOKEN)
    publisher._bot.send_video = AsyncMock()
    publisher._bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=4))
    missing_path = tmp_path / "does_not_exist.mp4"

    result = await publisher.publish(chat_id="@channel", text="подпись", video_path=missing_path)

    assert result.success is True
    publisher._bot.send_video.assert_not_awaited()
    publisher._bot.send_message.assert_awaited_once_with("@channel", "подпись", parse_mode=None)


@pytest.mark.asyncio
async def test_publish_with_video_sends_video(tmp_path):
    publisher = TelegramPublisher(FAKE_TOKEN)
    publisher._bot.send_video = AsyncMock(return_value=SimpleNamespace(message_id=4))
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"small fake video")

    result = await publisher.publish(chat_id="@channel", text="подпись", video_path=video_path)

    assert result.success is True
    assert result.message_id == 4
    publisher._bot.send_video.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_rejects_video_over_bot_api_size_limit_without_retrying(tmp_path):
    publisher = TelegramPublisher(FAKE_TOKEN)
    publisher._bot.send_video = AsyncMock()
    video_path = tmp_path / "huge.mp4"
    video_path.write_bytes(b"x")

    with patch("pathlib.Path.stat") as mock_stat:
        mock_stat.return_value = SimpleNamespace(st_size=BOT_API_FILE_LIMIT_BYTES + 1)
        result = await publisher.publish(chat_id="@channel", text="подпись", video_path=video_path)

    assert result.success is False
    assert "50 МБ" in result.error
    publisher._bot.send_video.assert_not_awaited()


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
