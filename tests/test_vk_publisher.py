from pathlib import Path
from unittest.mock import MagicMock, call, mock_open, patch

from app.core.publishing.token_bucket import token_key
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


def test_publish_with_video_and_photos_attaches_only_video():
    """Одинаковый пост во все соцсети (запрос пользователя 2026-07-05): при наличии
    видео фото НЕ прикрепляются — так VK совпадает с TG (тот при видео шлёт только
    видео). Раньше VK давал фото+видео, а TG только видео — расхождение."""
    publisher = make_publisher()
    publisher._api.video.save.return_value = {
        "upload_url": "http://upload-video", "video_id": 777, "owner_id": -123,
    }
    publisher._api.wall.post.return_value = {"post_id": 58}

    upload_response = MagicMock()
    upload_response.json.return_value = {"size": 12345}
    upload_response.raise_for_status = MagicMock()

    with (
        patch("app.core.publishing.vk_publisher.requests.post", return_value=upload_response),
        patch("builtins.open", mock_open(read_data=b"fake-bytes")),
    ):
        result = publisher.publish(
            group_id=123, text="новость",
            image_paths=[Path("a.jpg"), Path("b.jpg")], video_path=Path("clip.mp4"),
        )

    assert result.success is True
    _, kwargs = publisher._api.wall.post.call_args
    assert kwargs["attachments"] == "video-123_777"  # только видео, без фото
    publisher._api.photos.getWallUploadServer.assert_not_called()


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
    (личный аккаунт-админ группы), загрузка ДОЛЖНА идти через него."""
    publisher = VKPublisher.__new__(VKPublisher)
    publisher._api = MagicMock()  # group token
    publisher._upload_api = MagicMock()  # отдельный upload token
    publisher._upload_api.photos.getWallUploadServer.return_value = {"upload_url": "http://upload"}
    publisher._upload_api.photos.saveWallPhoto.return_value = [{"owner_id": -123, "id": 999}]
    publisher._upload_api.wall.post.return_value = {"post_id": 58}

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
    _, kwargs = publisher._upload_api.wall.post.call_args
    assert kwargs["attachments"] == "photo-123_999"


def test_publish_with_attachment_posts_via_upload_token_not_group_token():
    """КРИТИЧНО, найдено 2026-07-05 на реальном посте (id=170): photos.saveWallPhoto
    через личный upload_token сохраняет фото за ЛИЧНЫМ owner_id — групповой _api не
    имеет доступа к этому объекту (подтверждено вживую: photos.getById той же фоткой
    тем же токеном сразу после аплоада вернул "[200] Access denied"). wall.post
    групповым токеном с таким attachment не падает ошибкой — просто молча публикует
    БЕЗ вложения. Пост должен уйти через upload_token, когда есть вложение."""
    publisher = VKPublisher.__new__(VKPublisher)
    publisher._api = MagicMock()  # group token — НЕ должен звать wall.post при наличии фото
    publisher._upload_api = MagicMock()
    publisher._upload_api.photos.getWallUploadServer.return_value = {"upload_url": "http://upload"}
    publisher._upload_api.photos.saveWallPhoto.return_value = [{"owner_id": 861061590, "id": 999}]
    publisher._upload_api.wall.post.return_value = {"post_id": 59}

    upload_response = MagicMock()
    upload_response.json.return_value = {"photo": "p", "server": 1, "hash": "h"}
    upload_response.raise_for_status = MagicMock()

    with (
        patch("app.core.publishing.vk_publisher.requests.post", return_value=upload_response),
        patch("builtins.open", mock_open(read_data=b"fake-image-bytes")),
    ):
        result = publisher.publish(group_id=123, text="новость", image_paths=["fake.jpg"])

    assert result.success is True
    publisher._upload_api.wall.post.assert_called_once()
    publisher._api.wall.post.assert_not_called()
    _, kwargs = publisher._upload_api.wall.post.call_args
    assert kwargs["owner_id"] == -123
    assert kwargs["from_group"] == 1


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


def test_publish_fails_fast_on_rate_limit_without_retrying():
    """Антибан: VK error 6 (too many req/s) — НЕ долбить ретраями (это углубляет
    флуд-бан), выйти сразу и отдать код 6, чтобы circuit breaker открыл паузу."""
    publisher = make_publisher()

    class _ApiError(Exception):
        code = 6

    publisher._api.wall.post.side_effect = _ApiError("[6] Too many requests per second")

    with patch("app.core.publishing.vk_publisher.time.sleep"):
        result = publisher.publish(group_id=123, text="новость")

    assert result.success is False
    assert result.error_code == 6
    assert publisher._api.wall.post.call_count == 1  # без ретраев


def test_publish_text_only_paces_wall_post_with_group_key():
    """ТЗ: не более 2 запр/сек на токен — text-only пост пейсится ключом группового
    токена (личный токен вообще не тронут, когда вложений нет)."""
    bucket = MagicMock()
    with patch("app.core.publishing.vk_publisher.vk_api.VkApi") as mock_vk_api:
        mock_vk_api.return_value.get_api.return_value = MagicMock()
        publisher = VKPublisher("group-token", token_bucket=bucket)
    publisher._api.wall.post.return_value = {"post_id": 1}

    publisher.publish(group_id=123, text="новость")

    bucket.wait.assert_called_once_with(token_key("group-token"))


def test_publish_with_photo_paces_every_upload_call_with_upload_key():
    """Личный upload-токен грузит фото ДВУМЯ VK API-вызовами (getWallUploadServer +
    saveWallPhoto), плюс финальный wall.post уходит тем же токеном (см. регрессию
    2026-07-05 про owner_id) — все три пейсятся ключом ЛИЧНОГО токена, не группового."""
    bucket = MagicMock()
    group_api = MagicMock()
    upload_api = MagicMock()
    upload_api.photos.getWallUploadServer.return_value = {"upload_url": "http://upload"}
    upload_api.photos.saveWallPhoto.return_value = [{"owner_id": -123, "id": 999}]
    upload_api.wall.post.return_value = {"post_id": 2}

    with patch("app.core.publishing.vk_publisher.vk_api.VkApi") as mock_vk_api:
        mock_vk_api.side_effect = [
            MagicMock(get_api=MagicMock(return_value=group_api)),
            MagicMock(get_api=MagicMock(return_value=upload_api)),
        ]
        publisher = VKPublisher("group-token", upload_token="user-token", token_bucket=bucket)

    upload_response = MagicMock()
    upload_response.json.return_value = {"photo": "p", "server": 1, "hash": "h"}
    upload_response.raise_for_status = MagicMock()

    with (
        patch("app.core.publishing.vk_publisher.requests.post", return_value=upload_response),
        patch("builtins.open", mock_open(read_data=b"fake-image-bytes")),
    ):
        result = publisher.publish(group_id=123, text="новость", image_paths=["fake.jpg"])

    assert result.success is True
    expected_key = token_key("user-token")
    assert bucket.wait.call_args_list == [call(expected_key)] * 3
    group_api.wall.post.assert_not_called()


def test_publish_without_token_bucket_skips_pacing():
    """token_bucket не передан (напр. ручные пути UI/testpost) — публикация работает
    как раньше, без задержек."""
    publisher = VKPublisher.__new__(VKPublisher)
    publisher._api = MagicMock()
    publisher._upload_api = publisher._api
    publisher._api.wall.post.return_value = {"post_id": 1}

    result = publisher.publish(group_id=123, text="новость")

    assert result.success is True


def test_publish_with_photo_waits_cooldown_once_before_touching_personal_token():
    """Жёсткий кулдаун (ТЗ 2026-07-10) срабатывает РОВНО ОДИН раз за пост — перед
    началом загрузки медиа целиком, не между getWallUploadServer/saveWallPhoto/
    wall.post (иначе рвётся upload_url и публикация растягивается на часы)."""
    cooldown = MagicMock()
    group_api = MagicMock()
    upload_api = MagicMock()
    upload_api.photos.getWallUploadServer.return_value = {"upload_url": "http://upload"}
    upload_api.photos.saveWallPhoto.return_value = [{"owner_id": -123, "id": 999}]
    upload_api.wall.post.return_value = {"post_id": 3}

    with patch("app.core.publishing.vk_publisher.vk_api.VkApi") as mock_vk_api:
        mock_vk_api.side_effect = [
            MagicMock(get_api=MagicMock(return_value=group_api)),
            MagicMock(get_api=MagicMock(return_value=upload_api)),
        ]
        publisher = VKPublisher(
            "group-token", upload_token="user-token", cooldown_bucket=cooldown
        )

    upload_response = MagicMock()
    upload_response.json.return_value = {"photo": "p", "server": 1, "hash": "h"}
    upload_response.raise_for_status = MagicMock()

    with (
        patch("app.core.publishing.vk_publisher.requests.post", return_value=upload_response),
        patch("builtins.open", mock_open(read_data=b"fake-image-bytes")),
    ):
        result = publisher.publish(group_id=123, text="новость", image_paths=["fake.jpg"])

    assert result.success is True
    cooldown.wait.assert_called_once_with(token_key("user-token"))


def test_publish_text_only_never_touches_cooldown_bucket():
    """Нет медиа — личный токен вообще не трогаем, кулдаун ждать нечего."""
    cooldown = MagicMock()
    with patch("app.core.publishing.vk_publisher.vk_api.VkApi") as mock_vk_api:
        mock_vk_api.return_value.get_api.return_value = MagicMock()
        publisher = VKPublisher("group-token", cooldown_bucket=cooldown)
    publisher._api.wall.post.return_value = {"post_id": 1}

    publisher.publish(group_id=123, text="новость")

    cooldown.wait.assert_not_called()


def test_publish_fails_fast_on_auth_blocked():
    publisher = make_publisher()

    class _ApiError(Exception):
        code = 5

    publisher._api.wall.post.side_effect = _ApiError("[5] User authorization failed")

    with patch("app.core.publishing.vk_publisher.time.sleep"):
        result = publisher.publish(group_id=123, text="новость")

    assert result.success is False
    assert result.error_code == 5
    assert publisher._api.wall.post.call_count == 1
