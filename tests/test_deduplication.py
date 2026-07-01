from app.core.filtering.deduplication import (
    compute_simhash,
    find_similar_hash,
    is_duplicate_external_id,
)


def test_is_duplicate_external_id_true_when_present():
    assert is_duplicate_external_id("123", {"123", "456"}) is True


def test_is_duplicate_external_id_false_when_absent():
    assert is_duplicate_external_id("789", {"123", "456"}) is False


def test_find_similar_hash_detects_near_identical_text():
    original = compute_simhash("Президент подписал новый закон о бюджете на 2026 год")
    near_duplicate = compute_simhash("Президент подписал новый закон о бюджете на 2027 год")

    result = find_similar_hash(near_duplicate, [original], similarity_threshold=0.85)

    assert result == original


def test_find_similar_hash_returns_none_for_different_text():
    original = compute_simhash("Президент подписал закон о бюджете")
    unrelated = compute_simhash("Курс доллара вырос на бирже сегодня утром")

    result = find_similar_hash(unrelated, [original], similarity_threshold=0.85)

    assert result is None
