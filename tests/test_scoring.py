from app.config.loader import ScoringWeights
from app.core.filtering.scoring import compute_score

WEIGHTS = ScoringWeights(
    news_value=0.35,
    keyword_match=0.25,
    source_views=0.20,
    freshness=0.10,
    source_priority=0.10,
)


def test_compute_score_perfect_post_scores_100():
    score = compute_score(
        news_value_score=100,
        whitelist_matched=True,
        source_views=1000,
        min_views=500,
        post_age_hours=0,
        max_post_age_hours=24,
        source_priority=10,
        weights=WEIGHTS,
    )
    assert score == 100.0


def test_compute_score_weak_post_scores_low():
    score = compute_score(
        news_value_score=10,
        whitelist_matched=False,
        source_views=0,
        min_views=500,
        post_age_hours=23,
        max_post_age_hours=24,
        source_priority=0,
        weights=WEIGHTS,
    )
    assert score < 10


def test_compute_score_respects_weights_proportionally():
    only_news_value = compute_score(
        news_value_score=100,
        whitelist_matched=False,
        source_views=0,
        min_views=500,
        post_age_hours=24,
        max_post_age_hours=24,
        source_priority=0,
        weights=WEIGHTS,
    )
    assert only_news_value == 35.0
