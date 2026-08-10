"""Заливка ролика в разделы сообщества БЕЗ записи на стене (ТЗ 2026-08-10)."""
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.core.publishing.vk_publisher import POSTPONED_PREFIX, VKPublisher


def make_publisher() -> VKPublisher:
    publisher = VKPublisher.__new__(VKPublisher)
    publisher._api = MagicMock()
    publisher._upload_api = publisher._api
    publisher._upload_token = "personal"  # __new__ обходит __init__, задаём явно
    return publisher


def _video_file():
    handle = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    handle.write(b"fake-video-bytes")
    handle.flush()
    handle.close()
    return Path(handle.name)


def _upload_response() -> MagicMock:
    response = MagicMock()
    response.json.return_value = {"size": 1}
    response.raise_for_status = MagicMock()
    return response


def test_video_only_does_not_touch_the_wall():
    publisher = make_publisher()
    publisher._api.video.save.return_value = {
        "upload_url": "http://upload", "video_id": 777, "owner_id": -123,
    }
    path = _video_file()

    with patch("app.core.publishing.vk_publisher.requests.post", return_value=_upload_response()):
        result = publisher.publish_video_only(
            group_id=123, video_path=path, title="Фильм", description="описание",
        )

    assert result.success is True
    assert result.attachment == "video-123_777"
    assert result.post_id is None
    publisher._api.wall.post.assert_not_called()


def test_video_only_passes_title_and_description():
    publisher = make_publisher()
    publisher._api.video.save.return_value = {
        "upload_url": "http://upload", "video_id": 1, "owner_id": -123,
    }

    with patch("app.core.publishing.vk_publisher.requests.post", return_value=_upload_response()):
        publisher.publish_video_only(
            group_id=123, video_path=_video_file(), title="Фильм", description="большое SEO",
        )

    publisher._api.video.save.assert_called_once_with(
        name="Фильм", group_id=123, wallpost=0, description="большое SEO"
    )


def test_clip_uses_short_video_method():
    publisher = make_publisher()
    publisher._api.shortVideo.create.return_value = {
        "upload_url": "http://upload-clip", "video_id": 42, "owner_id": -123,
    }

    with patch("app.core.publishing.vk_publisher.requests.post", return_value=_upload_response()):
        result = publisher.publish_video_only(
            group_id=123, video_path=_video_file(), title="Клип", description="описание",
            as_clip=True,
        )

    assert result.attachment == "video-123_42"
    publisher._api.video.save.assert_not_called()


def test_clip_falls_back_to_plain_video_when_short_video_unavailable():
    """`shortVideo.*` нет в публичной схеме VK — метод может отвалиться в любой день.
    Клип, уехавший в раздел «Видео», для канала не потеря, а исключение — потеря."""
    publisher = make_publisher()
    publisher._api.shortVideo.create.side_effect = RuntimeError("Unknown method passed")
    publisher._api.video.save.return_value = {
        "upload_url": "http://upload", "video_id": 9, "owner_id": -123,
    }

    with patch("app.core.publishing.vk_publisher.requests.post", return_value=_upload_response()):
        result = publisher.publish_video_only(
            group_id=123, video_path=_video_file(), as_clip=True,
        )

    assert result.success is True
    assert result.attachment == "video-123_9"
    publisher._api.video.save.assert_called_once()


def test_video_only_postpones_when_pool_has_no_token():
    publisher = VKPublisher.__new__(VKPublisher)
    publisher._api = MagicMock()
    publisher._upload_api = publisher._api
    publisher._require_media = True
    publisher._upload_resolved = False
    publisher._upload_token_provider = lambda: None

    result = publisher.publish_video_only(group_id=123, video_path=Path("nope.mp4"))

    assert result.success is False
    assert result.error.startswith(POSTPONED_PREFIX)
    publisher._api.video.save.assert_not_called()


def test_video_only_reports_upload_failure():
    publisher = make_publisher()
    publisher._api.video.save.side_effect = RuntimeError("[7] нет прав")

    result = publisher.publish_video_only(group_id=123, video_path=Path("nope.mp4"))

    assert result.success is False
    assert "нет прав" in result.error
