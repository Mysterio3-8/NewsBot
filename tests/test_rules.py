from app.core.filtering.rules import (
    find_ad_marker,
    find_blacklisted_word,
    find_whitelisted_keywords,
    is_structural_non_news,
)


def test_is_structural_non_news_detects_poll():
    assert is_structural_non_news("poll") == "структурно не новость: poll"


def test_is_structural_non_news_allows_regular_post():
    assert is_structural_non_news("text") is None


def test_find_blacklisted_word_case_insensitive():
    result = find_blacklisted_word("Успей купить — скидка ждёт!", ["СКИДКА", "промокод"])
    assert result == "СКИДКА"


def test_find_blacklisted_word_returns_none_when_clean():
    result = find_blacklisted_word("Президент подписал закон", ["скидка", "промокод"])
    assert result is None


def test_find_whitelisted_keywords_returns_all_matches():
    result = find_whitelisted_keywords(
        "Госдума приняла закон о бюджете", ["Госдума", "Кремль", "закон"]
    )
    assert set(result) == {"Госдума", "закон"}


def test_find_ad_marker_detects_promo():
    assert find_ad_marker("Успей купить по промокоду СКИДКА30") is not None
    assert find_ad_marker("erid: 2Vfnxy... рекламодатель ООО Ромашка") is not None
    assert find_ad_marker("Лучший букмекер, ставки на спорт") is not None


def test_find_ad_marker_none_on_clean_movie_post():
    assert find_ad_marker("Мария Аронова рассказала о своей юности и мечтах") is None
    assert find_ad_marker("Новый триллер с Томом Харди выходит в прокат") is None
