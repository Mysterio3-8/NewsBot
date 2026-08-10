"""Новые настройки канала: разделы видео, фото поста, SEO (ТЗ 2026-08-10)."""
from app.core.channel_settings import ChannelSettings


def test_defaults_keep_previous_behaviour():
    """Канал без новых ключей обязан работать ровно как раньше."""
    settings = ChannelSettings.from_json("{}")
    assert settings.video_as_post is True
    assert settings.shuffle_images is False
    assert settings.max_images_per_post is None
    assert settings.promo_banner_mode == "drop"
    assert settings.seo_enabled is False


def test_reads_video_and_photo_settings():
    settings = ChannelSettings.from_json(
        '{"video_as_post": false, "shuffle_images": true, '
        '"max_images_per_post": 1, "promo_banner_mode": "restyle"}'
    )
    assert settings.video_as_post is False
    assert settings.shuffle_images is True
    assert settings.max_images_per_post == 1
    assert settings.promo_banner_mode == "restyle"


def test_reads_seo_settings():
    settings = ChannelSettings.from_json(
        '{"seo_enabled": true, "seo_hashtag_group": "kino", '
        '"seo_base_tags": ["кино"], "seo_search_phrases": ["{q} смотреть онлайн"], '
        '"seo_post_tag_limit": 6, "seo_video_tag_limit": 20}'
    )
    assert settings.seo_enabled is True
    assert settings.seo_hashtag_group == "kino"
    assert settings.seo_base_tags == ["кино"]
    assert settings.seo_search_phrases == ["{q} смотреть онлайн"]


def test_roundtrip_preserves_new_fields():
    original = ChannelSettings(
        filters_enabled=False,
        video_as_post=False,
        shuffle_images=True,
        max_images_per_post=1,
        promo_banner_mode="restyle",
        seo_enabled=True,
        seo_hashtag_group="kino",
        seo_base_tags=["кино", "фильмы"],
        seo_search_phrases=["{q} смотреть онлайн"],
        seo_post_tag_limit=6,
        seo_video_tag_limit=20,
    )
    assert ChannelSettings.from_json(original.to_json()) == original


def test_defaults_are_not_written_to_json():
    """to_json пишет только отличия от дефолта — иначе merge в проде раздувается."""
    payload = ChannelSettings().to_json()
    for key in ("video_as_post", "shuffle_images", "promo_banner_mode", "seo_enabled"):
        assert key not in payload


def test_seo_profile_carries_settings_and_links():
    settings = ChannelSettings(
        seo_hashtag_group="kino",
        seo_base_tags=["кино"],
        seo_search_phrases=["{q} трейлер"],
        seo_post_tag_limit=3,
        seo_video_tag_limit=9,
    )
    profile = settings.seo_profile(["📲 Telegram: https://t.me/x"])

    assert profile.hashtag_group == "kino"
    assert profile.base_tags == ["кино"]
    assert profile.post_tag_limit == 3
    assert profile.video_tag_limit == 9
    assert profile.links == ["📲 Telegram: https://t.me/x"]
