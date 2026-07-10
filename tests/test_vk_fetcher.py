from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import app.core.monitoring.vk_fetcher as vk_fetcher_module
from app.core.publishing.token_bucket import token_key
from app.core.monitoring.vk_fetcher import VKFetcher, vk_post_to_fetched_post


def make_item(**overrides):
    defaults = dict(
        id=42,
        text="Текст поста",
        date=int(datetime.now(timezone.utc).timestamp()),
        views={"count": 500},
        attachments=[],
        is_pinned=0,
        marked_as_ads=0,
    )
    defaults.update(overrides)
    return defaults


def test_vk_post_to_fetched_post_maps_regular_post():
    post = vk_post_to_fetched_post(make_item())

    assert post.external_id == "42"
    assert post.text == "Текст поста"
    assert post.post_type == "text"
    assert post.views == 500
    assert post.has_media is False


def test_vk_post_to_fetched_post_detects_media():
    post = vk_post_to_fetched_post(make_item(attachments=[{"type": "photo"}]))
    assert post.has_media is True


def test_vk_post_to_fetched_post_extracts_largest_photo_url():
    attachment = {
        "type": "photo",
        "photo": {
            "sizes": [
                {"type": "m", "url": "https://vk.com/small.jpg", "width": 130, "height": 87},
                {"type": "x", "url": "https://vk.com/large.jpg", "width": 807, "height": 540},
            ]
        },
    }
    post = vk_post_to_fetched_post(make_item(attachments=[attachment]))
    assert post.media_urls == ["https://vk.com/large.jpg"]


def test_vk_post_to_fetched_post_no_media_urls_when_no_photo_attachments():
    post = vk_post_to_fetched_post(make_item(attachments=[{"type": "poll"}]))
    assert post.media_urls == []


def test_vk_post_to_fetched_post_detects_ad():
    post = vk_post_to_fetched_post(make_item(marked_as_ads=1))
    assert post.post_type == "ad"


def test_vk_post_to_fetched_post_detects_pinned():
    post = vk_post_to_fetched_post(make_item(is_pinned=1))
    assert post.post_type == "pinned"


def test_vk_post_to_fetched_post_detects_poll():
    post = vk_post_to_fetched_post(make_item(attachments=[{"type": "poll"}]))
    assert post.post_type == "poll"


def test_vk_post_to_fetched_post_missing_views_defaults_to_zero():
    item = make_item()
    item["views"] = {}
    post = vk_post_to_fetched_post(item)
    assert post.views == 0


def test_fetch_recent_posts_filters_by_age():
    now = datetime.now(timezone.utc)
    fresh = make_item(id=2, date=int(now.timestamp()))
    stale = make_item(id=1, date=int((now - timedelta(hours=100)).timestamp()))

    fetcher = VKFetcher.__new__(VKFetcher)
    fetcher._api = MagicMock()
    fetcher._api.wall.get.return_value = {"items": [fresh, stale]}

    posts = fetcher.fetch_recent_posts(123, max_age_hours=24)

    assert [p.external_id for p in posts] == ["2"]
    fetcher._api.wall.get.assert_called_once_with(owner_id=-123, count=50)


def test_fetch_recent_posts_skips_known_external_ids_before_downloading(monkeypatch):
    now = datetime.now(timezone.utc)
    known = make_item(id=1, date=int(now.timestamp()))
    new = make_item(id=2, date=int(now.timestamp()))

    fetcher = VKFetcher.__new__(VKFetcher)
    fetcher._api = MagicMock()
    fetcher._api.wall.get.return_value = {"items": [new, known]}

    download_mock = MagicMock(return_value=[])
    monkeypatch.setattr(fetcher, "_download_photos", download_mock)

    posts = fetcher.fetch_recent_posts(123, max_age_hours=24, known_external_ids={"1"})

    assert [p.external_id for p in posts] == ["2"]
    download_mock.assert_called_once_with("2", [])


def test_fetch_recent_posts_downloads_photos_to_local_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(vk_fetcher_module, "VK_MEDIA_DIR", tmp_path)

    attachment = {
        "type": "photo",
        "photo": {"sizes": [{"type": "x", "url": "https://vk.com/photo.jpg", "width": 800, "height": 600}]},
    }
    item = make_item(id=5, attachments=[attachment])

    fetcher = VKFetcher.__new__(VKFetcher)
    fetcher._api = MagicMock()
    fetcher._api.wall.get.return_value = {"items": [item]}

    fake_response = MagicMock()
    fake_response.content = b"fake-jpeg-bytes"
    fake_response.raise_for_status = MagicMock()
    monkeypatch.setattr(vk_fetcher_module.requests, "get", MagicMock(return_value=fake_response))

    posts = fetcher.fetch_recent_posts(123, max_age_hours=24)

    assert len(posts) == 1
    [local_path] = posts[0].media_urls
    assert local_path == str(tmp_path / "5_0.jpg")
    assert (tmp_path / "5_0.jpg").read_bytes() == b"fake-jpeg-bytes"


def test_fetch_recent_posts_paces_wall_get_with_token_bucket():
    """ТЗ: минимизировать/лимитировать личный VK-токен (VK_USER_TOKEN, чтение
    источников) — не более 2 запр/сек на токен."""
    now = datetime.now(timezone.utc)
    item = make_item(id=2, date=int(now.timestamp()))

    fetcher = VKFetcher.__new__(VKFetcher)
    fetcher._api = MagicMock()
    fetcher._api.wall.get.return_value = {"items": [item]}
    bucket = MagicMock()
    fetcher._bucket = bucket
    fetcher._bucket_key = "fake-key"

    fetcher.fetch_recent_posts(123, max_age_hours=24)

    bucket.wait.assert_called_once_with("fake-key")


def test_init_derives_bucket_key_from_user_token():
    with patch("app.core.monitoring.vk_fetcher.vk_api.VkApi") as mock_vk_api:
        mock_vk_api.return_value.get_api.return_value = MagicMock()
        fetcher = VKFetcher("secret-token")

    assert fetcher._bucket_key == token_key("secret-token")
    assert fetcher._bucket is None


def test_fetch_recent_posts_without_token_bucket_skips_pacing():
    now = datetime.now(timezone.utc)
    item = make_item(id=2, date=int(now.timestamp()))

    fetcher = VKFetcher.__new__(VKFetcher)
    fetcher._api = MagicMock()
    fetcher._api.wall.get.return_value = {"items": [item]}

    posts = fetcher.fetch_recent_posts(123, max_age_hours=24)

    assert [p.external_id for p in posts] == ["2"]


def test_fetch_recent_posts_skips_photo_that_fails_to_download(tmp_path, monkeypatch):
    monkeypatch.setattr(vk_fetcher_module, "VK_MEDIA_DIR", tmp_path)

    attachment = {
        "type": "photo",
        "photo": {"sizes": [{"type": "x", "url": "https://vk.com/photo.jpg", "width": 800, "height": 600}]},
    }
    item = make_item(id=6, attachments=[attachment])

    fetcher = VKFetcher.__new__(VKFetcher)
    fetcher._api = MagicMock()
    fetcher._api.wall.get.return_value = {"items": [item]}

    monkeypatch.setattr(
        vk_fetcher_module.requests, "get", MagicMock(side_effect=ConnectionError("boom"))
    )

    posts = fetcher.fetch_recent_posts(123, max_age_hours=24)

    assert len(posts) == 1
    assert posts[0].media_urls == []
