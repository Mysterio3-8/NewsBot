"""Настройки канала (ChannelSettings) — парсинг Channel.settings_json."""
from app.core.channel_settings import ChannelSettings


def test_from_json_empty_returns_defaults():
    assert ChannelSettings.from_json(None) == ChannelSettings(filters_enabled=True)
    assert ChannelSettings.from_json("") == ChannelSettings(filters_enabled=True)
    assert ChannelSettings.from_json("{}").filters_enabled is True


def test_from_json_reads_filters_disabled():
    settings = ChannelSettings.from_json('{"filters_enabled": false}')
    assert settings.filters_enabled is False
    assert settings.max_posts_per_day is None


def test_from_json_reads_max_posts_per_day():
    settings = ChannelSettings.from_json('{"filters_enabled": false, "max_posts_per_day": 4}')
    assert settings.max_posts_per_day == 4


def test_to_json_roundtrip():
    original = ChannelSettings(filters_enabled=False, max_posts_per_day=3)
    assert ChannelSettings.from_json(original.to_json()) == original


def test_from_json_reads_interval_and_footer():
    s = ChannelSettings.from_json(
        '{"filters_enabled": false, "max_posts_per_day": 3, '
        '"min_interval_minutes": 480, "tg_footer_url": "https://t.me/x"}'
    )
    assert s.min_interval_minutes == 480
    assert s.tg_footer_url == "https://t.me/x"


def test_to_json_roundtrip_with_interval_and_footer():
    original = ChannelSettings(
        filters_enabled=False, max_posts_per_day=3,
        min_interval_minutes=480, tg_footer_url="https://t.me/x",
    )
    assert ChannelSettings.from_json(original.to_json()) == original


def test_from_json_reads_movie_mode():
    s = ChannelSettings.from_json(
        '{"filters_enabled": false, "image_query_mode": "movie_title", '
        '"image_providers_order": ["google"]}'
    )
    assert s.image_query_mode == "movie_title"
    assert s.image_providers_order == ["google"]


def test_default_image_query_mode_is_generic():
    assert ChannelSettings().image_query_mode == "generic"
    assert ChannelSettings.from_json(None).image_providers_order is None


def test_to_json_roundtrip_with_movie_mode():
    original = ChannelSettings(
        filters_enabled=False,
        image_query_mode="movie_title",
        image_providers_order=["google"],
    )
    assert ChannelSettings.from_json(original.to_json()) == original


def test_from_json_reads_logo_and_photo_design():
    s = ChannelSettings.from_json(
        '{"filters_enabled": false, "logo_path": "assets/filmlogo.png", "photo_design": false}'
    )
    assert s.logo_path == "assets/filmlogo.png"
    assert s.photo_design is False


def test_to_json_roundtrip_with_logo_and_design():
    original = ChannelSettings(
        filters_enabled=False, logo_path="assets/filmlogo.png", photo_design=False
    )
    assert ChannelSettings.from_json(original.to_json()) == original


def test_from_json_reads_rewrite_prompt_and_max_length():
    s = ChannelSettings.from_json(
        '{"filters_enabled": false, "rewrite_prompt": "rewrite_kino", "rewrite_max_length": 1200}'
    )
    assert s.rewrite_prompt == "rewrite_kino"
    assert s.rewrite_max_length == 1200


def test_to_json_roundtrip_with_rewrite_overrides():
    original = ChannelSettings(
        filters_enabled=False, rewrite_prompt="rewrite_kino", rewrite_max_length=1200
    )
    assert ChannelSettings.from_json(original.to_json()) == original


def test_from_json_reads_split_collage_and_uniquify():
    s = ChannelSettings.from_json('{"split_collage": true, "uniquify_images": true}')
    assert s.split_collage is True
    assert s.uniquify_images is True


def test_split_and_uniquify_default_false():
    s = ChannelSettings.from_json("{}")
    assert s.split_collage is False
    assert s.uniquify_images is False


def test_to_json_roundtrip_with_split_and_uniquify():
    original = ChannelSettings(filters_enabled=False, split_collage=True, uniquify_images=True)
    assert ChannelSettings.from_json(original.to_json()) == original


def test_logo_and_design_default_none():
    s = ChannelSettings.from_json(None)
    assert s.logo_path is None
    assert s.photo_design is None


def test_to_json_omits_generic_mode_for_compact_output():
    """generic — дефолт, не нужно засорять JSON лишним полем для News-канала."""
    payload = ChannelSettings().to_json()
    assert "image_query_mode" not in payload
