from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

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
