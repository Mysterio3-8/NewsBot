from app.config.loader import FooterConfig
from app.core.publishing.footer import (
    FooterLinks,
    build_channel_footer,
    build_footer_links_from_config,
    build_html_footer,
    build_vk_footer,
)


def _footer_config() -> FooterConfig:
    return FooterConfig(
        enabled=False,
        label="x",
        telegram_url="https://t.me/NewsThreeWord",
        vk_url="https://vk.com/club233689032",
        telegram_signature="🔢 Новости в трёх словах",
        subscribe_cta="Подписывайтесь на Telegram-канал:",
    )


def test_build_channel_footer_uses_channel_url_and_signature():
    links = build_channel_footer(
        "https://t.me/NewsThreeWord", "🔢 Новости в трёх словах", _footer_config(), None
    )
    assert links.telegram_url == "https://t.me/NewsThreeWord"
    assert links.telegram_signature == "🔢 Новости в трёх словах"
    assert links.subscribe_cta == "Подписывайтесь на Telegram-канал:"


def test_build_channel_footer_falls_back_to_config_signature_when_channel_has_none():
    links = build_channel_footer("https://t.me/x", None, _footer_config(), None)
    assert links.telegram_signature == "🔢 Новости в трёх словах"


def test_build_channel_footer_returns_fallback_without_channel_url():
    fallback = FooterLinks(telegram_url="https://t.me/global")
    assert build_channel_footer(None, None, _footer_config(), fallback) is fallback


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


def test_html_footer_includes_vk_link_as_second_line():
    """ТЗ 2026-07-28: гнать аудиторию из TG ещё и во ВКонтакте."""
    links = FooterLinks(
        telegram_url="https://t.me/kinobestfilmss",
        telegram_signature="Больше фильмов",
        vk_url="https://vk.com/public240120678",
        vk_signature="Больше контента в нашем VK",
    )

    footer = build_html_footer(links)

    lines = footer.split("\n")
    assert len(lines) == 2
    assert 'href="https://t.me/kinobestfilmss"' in lines[0]
    assert 'href="https://vk.com/public240120678"' in lines[1]


def test_html_footer_without_vk_stays_single_line():
    links = FooterLinks(telegram_url="https://t.me/x", telegram_signature="Канал")

    assert "\n" not in build_html_footer(links)


def test_channel_footer_carries_vk_url():
    links = build_channel_footer(
        "https://t.me/x", "Подпись", _footer_config(), None, vk_url="https://vk.com/public1"
    )

    assert links.vk_url == "https://vk.com/public1"
