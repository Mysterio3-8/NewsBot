"""Локальные эвристики фильтрации до вызова LLM (раздел 8 SPEC.md)."""
from __future__ import annotations

STRUCTURAL_NON_NEWS_TYPES = {"poll", "pinned", "service", "ad"}

# Маркеры рекламы в тексте (VK-флаг marked_as_ads ловит не всё). Запрос пользователя
# 2026-07-14: рекламные посты не брать. Проверяется во ВСЕХ режимах, включая «лить всё».
# Расширено 2026-07-18 реальными примерами нативной рекламы от пользователя (Яндекс-
# пополнение, ковры, гель для труб OZON/WB, толстовки, статуэтки по фото).
AD_MARKERS = frozenset({
    "erid",
    "реклама",
    "рекламодатель",
    "на правах рекламы",
    "промокод",
    "по промокоду",
    "промокоду",
    # «распрода» ловит распродажа/распродаём/распродаем остатки
    "распрода",
    "кэшбэк",
    "рассрочк",
    "букмекер",
    "казино",
    "ставки на спорт",
    "оформить заказ",
    "оформи заказ",
    "переходи по ссылке",
    "успей купить",
    # Ссылки-шортенеры: в постах источников это практически всегда партнёрская реклама.
    "vk.cc/",
    "clck.ru/",
    "bit.ly/",
    "ya.cc/",
    # Маркетплейсы и «ссылка на магазин»
    "ozon.ru",
    "wildberries.ru",
    "ссылка на ozon",
    "ссылка на wb",
    "ссылка на озон",
    "артикул",
    "маркетплейс",
    # Продающие CTA-обороты
    "заказывайте",
    "закажите",
    "пока не раскупили",
    "по выгодной цене",
    "со скидкой",
    "узнать цены",
    "узнать цену",
    "узнай цену",
    "напиши «привет»",
    'напиши "привет"',
    "в личные сообщения",
    "в личных сообщениях",
    # Товарные описания, которых не бывает в новостях/кино
    "размерный ряд",
    "быстрая доставка",
    "доставка по всей",
    "собственное производство",
    # Финансовые заманухи («пополните счёт — получите бонус»)
    "пополните счет",
    "пополните счёт",
    "пополни счет",
    "пополни счёт",
    "подробнее на сайте",
})


def is_structural_non_news(post_type: str) -> str | None:
    """Проверка по типу поста (опрос/закреп/служебное сообщение), не по тексту."""
    if post_type in STRUCTURAL_NON_NEWS_TYPES:
        return f"структурно не новость: {post_type}"
    return None


def find_ad_marker(text: str) -> str | None:
    """Первый найденный маркер рекламы в тексте или None. Рекламные посты не публикуем."""
    lowered = text.lower()
    for marker in AD_MARKERS:
        if marker in lowered:
            return marker
    return None


def find_blacklisted_word(text: str, blacklist_keywords: list[str]) -> str | None:
    lowered_text = text.lower()
    for word in blacklist_keywords:
        if word.lower() in lowered_text:
            return word
    return None


def find_whitelisted_keywords(text: str, whitelist_keywords: list[str]) -> list[str]:
    lowered_text = text.lower()
    return [word for word in whitelist_keywords if word.lower() in lowered_text]
