from app.config.loader import FooterConfig
from app.core.publishing.footer import (
    FooterLinks,
    build_footer_links_from_config,
    build_html_footer,
    build_vk_footer,
)


def test_build_html_footer_with_both_links():
    links = FooterLinks(
        label="Подписывайтесь на нас",
        telegram_url="https://t.me/NewsThreeWord",
        vk_url="https://vk.com/club123456",
    )
    result = build_html_footer(links)
    assert result == (
        "Подписывайтесь на нас: "
        '<a href="https://t.me/NewsThreeWord">Telegram</a> | '
        '<a href="https://vk.com/club123456">VK</a>'
    )


def test_build_html_footer_with_only_telegram():
    links = FooterLinks(label="Подписывайтесь на нас", telegram_url="https://t.me/x")
    assert build_html_footer(links) == 'Подписывайтесь на нас: <a href="https://t.me/x">Telegram</a>'


def test_build_html_footer_empty_when_no_links():
    assert build_html_footer(FooterLinks(label="x")) == ""


def test_build_vk_footer_uses_internal_club_reference_for_vk_link():
    """Регрессия: полный URL на СВОЮ же VK-группу в скобках не разлинковывался
    надёжно на реальной публикации — нужен внутренний формат [club<id>|Текст]."""
    links = FooterLinks(
        label="Подписывайтесь на нас",
        telegram_url="https://t.me/NewsThreeWord",
        vk_url="https://vk.com/club123456",
    )
    result = build_vk_footer(links)
    assert result == (
        "Подписывайтесь на нас: "
        "[https://t.me/NewsThreeWord|Telegram] [club123456|VK]"
    )


def test_build_vk_footer_falls_back_to_raw_url_when_not_club_or_public():
    links = FooterLinks(label="Подписывайтесь на нас", vk_url="https://vk.com/somevanityname")
    result = build_vk_footer(links)
    assert result == "Подписывайтесь на нас: [https://vk.com/somevanityname|VK]"


def test_build_vk_footer_empty_when_no_links():
    assert build_vk_footer(FooterLinks(label="x")) == ""


def test_build_footer_links_from_config_returns_none_when_disabled():
    config = FooterConfig(enabled=False, label="x", telegram_url="https://t.me/x", vk_url="")
    assert build_footer_links_from_config(config) is None


def test_build_footer_links_from_config_maps_fields_and_empty_vk_to_none():
    config = FooterConfig(enabled=True, label="Подписывайтесь", telegram_url="https://t.me/x", vk_url="")
    links = build_footer_links_from_config(config)
    assert links == FooterLinks(label="Подписывайтесь", telegram_url="https://t.me/x", vk_url=None)
