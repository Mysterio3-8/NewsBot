from app.core.filtering.rules import (
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
