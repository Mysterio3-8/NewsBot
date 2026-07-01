"""Взвешенный скоринг поста (раздел 11 SPEC.md)."""
from __future__ import annotations

from app.config.loader import ScoringWeights


def compute_score(
    *,
    news_value_score: float,
    whitelist_matched: bool,
    source_views: int,
    min_views: int,
    post_age_hours: float,
    max_post_age_hours: float,
    source_priority: int,
    weights: ScoringWeights,
) -> float:
    keyword_score = 100.0 if whitelist_matched else 0.0
    views_score = _views_score(source_views, min_views)
    freshness_score = _freshness_score(post_age_hours, max_post_age_hours)
    priority_score = min(100.0, (source_priority / 10) * 100)

    total = (
        news_value_score * weights.news_value
        + keyword_score * weights.keyword_match
        + views_score * weights.source_views
        + freshness_score * weights.freshness
        + priority_score * weights.source_priority
    )
    return round(total, 2)


def _views_score(source_views: int, min_views: int) -> float:
    if min_views <= 0:
        return 0.0
    return min(100.0, (source_views / min_views) * 100)


def _freshness_score(post_age_hours: float, max_post_age_hours: float) -> float:
    if max_post_age_hours <= 0:
        return 0.0
    return max(0.0, 100.0 * (1 - post_age_hours / max_post_age_hours))
