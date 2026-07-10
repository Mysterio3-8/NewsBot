from app.config.loader import FooterConfig
from app.core.publishing.footer import (
    FooterLinks,
    build_footer_links_from_config,
    build_html_footer,
    build_vk_footer,
)


def test_build_html_footer_is_branded_hyperlink_to_telegram():
    """TG (ТЗ 2026-07-10): фирменная подпись именованной гиперссылкой на канал."""
    links = FooterLinks(
        telegram_url="https://t.me/NewsThreeWord",
        telegram_signature="🔢 Новости в трёх словах",
    )
    assert build_html_footer(links) == (
        '<a href="https://t.me/NewsThreeWord">🔢 Новости в трёх словах</a>'
    )


def test_build_html_footer_empty_when_no_telegram_url():
    assert build_html_footer(FooterLinks(telegram_url=None)) == ""


def test_build_vk_footer_is_subscribe_cta_plus_plain_url():
    """VK/Instagram (ТЗ 2026-07-10): призыв подписаться + голый URL. Голый URL —
    намеренно: в VK он кликабелен автоматически, скобки внешние домены не линкуют."""
    links = FooterLinks(
        telegram_url="https://t.me/NewsThreeWord",
        subscribe_cta="Подписывайтесь на Telegram-канал:",
    )
    assert build_vk_footer(links) == (
        "Подписывайтесь на Telegram-канал:\nhttps://t.me/NewsThreeWord"
    )


def test_build_vk_footer_empty_when_no_telegram_url():
    assert build_vk_footer(FooterLinks(telegram_url=None)) == ""


def test_build_footer_links_from_config_returns_none_when_disabled():
    config = FooterConfig(
        enabled=False, label="x", telegram_url="https://t.me/x", vk_url=""
    )
    assert build_footer_links_from_config(config) is None


def test_build_footer_links_from_config_maps_signature_and_cta():
    config = FooterConfig(
        enabled=True,
        label="устар.",
        telegram_url="https://t.me/x",
        vk_url="",
        telegram_signature="🔢 Бренд",
        subscribe_cta="Подпишись:",
    )
    assert build_footer_links_from_config(config) == FooterLinks(
        telegram_url="https://t.me/x",
        telegram_signature="🔢 Бренд",
        subscribe_cta="Подпишись:",
    )


def test_footer_config_defaults_allow_missing_yaml_keys():
    """Старый config.yaml без новых ключей грузится на дефолтах, не падает."""
    config = FooterConfig(enabled=True, label="x", telegram_url="https://t.me/x", vk_url="")
    assert config.telegram_signature == "Новости в трёх словах"
    assert config.subscribe_cta == "Подписывайтесь на Telegram-канал:"
