from unittest.mock import MagicMock, mock_open, patch

from app.core.publishing.vk_publisher import VKPublisher


def make_publisher() -> VKPublisher:
    publisher = VKPublisher.__new__(VKPublisher)
    publisher._api = MagicMock()
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


def test_publish_retries_and_fails_after_exhausting_attempts():
    publisher = make_publisher()
    publisher._api.wall.post.side_effect = Exception("сеть недоступна")

    with patch("app.core.publishing.vk_publisher.time.sleep"):
        result = publisher.publish(group_id=123, text="новость")

    assert result.success is False
    assert "сеть недоступна" in result.error
    assert publisher._api.wall.post.call_count == 4
