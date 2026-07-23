"""Заголовок и описание для загрузки на YouTube (ссылки на VK+TG, лимиты, Shorts)."""
from __future__ import annotations

from app.core.publishing.youtube_description import (
    YOUTUBE_TITLE_LIMIT,
    build_vk_group_url,
    build_youtube_description,
    build_youtube_title,
)


def test_vk_group_url_from_numeric_destination():
    assert build_vk_group_url("240120678") == "https://vk.com/public240120678"


def test_vk_group_url_passes_through_full_url():
    assert build_vk_group_url("https://vk.com/kino") == "https://vk.com/kino"


def test_vk_group_url_none_when_empty():
    assert build_vk_group_url(None) is None
    assert build_vk_group_url("") is None


def test_short_title_gets_shorts_tag():
    title = build_youtube_title("Астрал. Амулет зла", is_short=True)

    assert title.endswith(" #Shorts")
    assert "Астрал" in title


def test_film_title_has_no_shorts_tag():
    assert "#Shorts" not in build_youtube_title("Астрал. Амулет зла", is_short=False)


def test_long_title_is_truncated_within_limit():
    title = build_youtube_title("Очень длинное название фильма " * 10, is_short=True)

    assert len(title) <= YOUTUBE_TITLE_LIMIT
    assert title.endswith(" #Shorts")


def test_description_includes_both_links():
    description = build_youtube_description(
        "Крутой фильм", vk_url="https://vk.com/public240120678", tg_url="https://t.me/kinobestfilmss"
    )

    assert "Крутой фильм" in description
    assert "https://vk.com/public240120678" in description
    assert "https://t.me/kinobestfilmss" in description


def test_description_skips_missing_links():
    description = build_youtube_description("Текст", vk_url=None, tg_url=None)

    assert description == "Текст"
