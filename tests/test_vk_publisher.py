from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

from app.core.publishing.vk_publisher import VKPublisher


def make_publisher() -> VKPublisher:
    publisher = VKPublisher.__new__(VKPublisher)
    publisher._api = MagicMock()
    publisher._upload_api = publisher._api  # дефолт: без отдельного upload_token
    return publisher


def test_publish_text_only_calls_wall_post():
    publisher = make_publisher()
    publisher._api.wall.post.return_value = {"post_id": 55}

    result = publisher.publish(group_id=123, text="новость")

    assert result.success is True
    assert result.post_id == 55
    publisher._api.wall.post.assert_called_once_with(
        owner_id=-123, message="новость", attachments=None, from_group=1
    )


def test_publish_with_image_uploads_and_attaches():
    publisher = make_publisher()
    publisher._api.photos.getWallUploadServer.return_value = {"upload_url": "http://upload"}
    publisher._api.photos.saveWallPhoto.return_value = [{"owner_id": -123, "id": 999}]
    publisher._api.wall.post.return_value = {"post_id": 56}

    upload_response = MagicMock()
    upload_response.json.return_value = {"photo": "p", "server": 1, "hash": "h"}
    upload_response.raise_for_status = MagicMock()

    with (
        patch("app.core.publishing.vk_publisher.requests.post", return_value=upload_response),
        patch("builtins.open", mock_open(read_data=b"fake-image-bytes")),
    ):
        result = publisher.publish(group_id=123, text="новость", image_paths=["fake.jpg"])

    assert result.success is True
    _, kwargs = publisher._api.wall.post.call_args
    assert kwargs["attachments"] == "photo-123_999"


def test_publish_with_video_uploads_and_attaches():
    publisher = make_publisher()
    publisher._api.video.save.return_value = {
        "upload_url": "http://upload-video", "video_id": 777, "owner_id": -123,
    }
    publisher._api.wall.post.return_value = {"post_id": 57}

    upload_response = MagicMock()
    upload_response.json.return_value = {"size": 12345}
    upload_response.raise_for_status = MagicMock()

    with (
        patch("app.core.publishing.vk_publisher.requests.post", return_value=upload_response),
        patch("builtins.open", mock_open(read_data=b"fake-video-bytes")),
    ):
        result = publisher.publish(group_id=123, text="новость", video_path=Path("fake.mp4"))

    assert result.success is True
    _, kwargs = publisher._api.wall.post.call_args
    assert kwargs["attachments"] == "video-123_777"
    # wallpost=0 (int), не Python bool False — vk_api сериализует False в строку "False",
    # VK отвечает [10] Internal server error (подтверждено вживую 2026-07-04).
    publisher._api.video.save.assert_called_once_with(
        name="fake", group_id=123, wallpost=0
    )


def test_publish_posts_text_only_when_photo_upload_fails():
    """Регрессия: групповой токен не может загрузить фото (VK error 27) — пост должен
    уйти текстом, а не упасть целиком (главное — публикация во все сети)."""
    publisher = make_publisher()
    publisher._api.photos.getWallUploadServer.side_effect = Exception(
        "[27] Group authorization failed"
    )
    publisher._api.wall.post.return_value = {"post_id": 60}

    result = publisher.publish(group_id=123, text="новость", image_paths=["fake.jpg"])

    assert result.success is True
    assert result.post_id == 60
    _, kwargs = publisher._api.wall.post.call_args
    assert kwargs["attachments"] is None  # без вложения, но пост опубликован


def test_upload_photo_uses_separate_upload_token_when_configured():
    """group-токен не умеет photos.* (ошибка 27) — если задан отдельный upload_token
    (личный аккаунт-админ группы), загрузка ДОЛЖНА идти через него, а wall.post —
    всегда через group_token, чтобы минимизировать автоматизацию на личном аккаунте."""
    publisher = VKPublisher.__new__(VKPublisher)
    publisher._api = MagicMock()  # group token — только для wall.post
    publisher._upload_api = MagicMock()  # отдельный upload token
    publisher._upload_api.photos.getWallUploadServer.return_value = {"upload_url": "http://upload"}
    publisher._upload_api.photos.saveWallPhoto.return_value = [{"owner_id": -123, "id": 999}]
    publisher._api.wall.post.return_value = {"post_id": 58}

    upload_response = MagicMock()
    upload_response.json.return_value = {"photo": "p", "server": 1, "hash": "h"}
    upload_response.raise_for_status = MagicMock()

    with (
        patch("app.core.publishing.vk_publisher.requests.post", return_value=upload_response),
        patch("builtins.open", mock_open(read_data=b"fake-image-bytes")),
    ):
        result = publisher.publish(group_id=123, text="новость", image_paths=["fake.jpg"])

    assert result.success is True
    publisher._upload_api.photos.getWallUploadServer.assert_called_once()
    publisher._api.photos.getWallUploadServer.assert_not_called()
    _, kwargs = publisher._api.wall.post.call_args
    assert kwargs["attachments"] == "photo-123_999"


def test_init_without_upload_token_reuses_group_api_for_uploads():
    with patch("app.core.publishing.vk_publisher.vk_api.VkApi") as mock_vk_api:
        mock_vk_api.return_value.get_api.return_value = MagicMock()
        publisher = VKPublisher("group-token")

    assert publisher._upload_api is publisher._api
    mock_vk_api.assert_called_once_with(token="group-token")


def test_init_with_upload_token_creates_separate_api():
    with patch("app.core.publishing.vk_publisher.vk_api.VkApi") as mock_vk_api:
        mock_vk_api.side_effect = [MagicMock(), MagicMock()]
        publisher = VKPublisher("group-token", upload_token="user-token")

    assert publisher._upload_api is not publisher._api
    assert mock_vk_api.call_args_list == [
        ((), {"token": "group-token"}),
        ((), {"token": "user-token"}),
    ]


def test_publish_retries_and_fails_after_exhausting_attempts():
    publisher = make_publisher()
    publisher._api.wall.post.side_effect = Exception("сеть недоступна")

    with patch("app.core.publishing.vk_publisher.time.sleep"):
        result = publisher.publish(group_id=123, text="новость")

    assert result.success is False
    assert "сеть недоступна" in result.error
    assert publisher._api.wall.post.call_count == 4
