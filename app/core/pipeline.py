"""Оркестрация обработки поста: new → filtering → classified → rewritten → queued
(раздел 13.1 SPEC.md). Публикация — отдельный шаг по нажатию кнопки в UI.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config.loader import FiltersConfig, RewriteConfig, ScoringWeights
from app.core.filtering.deduplication import compute_simhash, find_similar_hash
from app.core.filtering.rules import (
    find_blacklisted_word,
    find_whitelisted_keywords,
    is_structural_non_news,
)
from app.core.filtering.scoring import compute_score
from app.core.llm.classifier import ClassificationError, classify_post
from app.core.llm.client import LLMClient, LLMUnavailableError
from app.core.llm.headline_generator import generate_headlines
from app.core.llm.rewriter import rewrite_post
from app.core.monitoring.models import FetchedPost
from app.db.models import ProcessedPost, RejectedPost, Source
from app.db.repository import Repository

logger = logging.getLogger("monitoring")


@dataclass(frozen=True)
class ProcessingOutcome:
    accepted: ProcessedPost | None
    rejected: RejectedPost | None


def process_fetched_post(
    repo: Repository,
    source: Source,
    post: FetchedPost,
    *,
    llm_client: LLMClient,
    filters: FiltersConfig,
    scoring_weights: ScoringWeights,
    rewrite_config: RewriteConfig,
    max_post_age_hours: float,
) -> ProcessingOutcome | None:
    """None означает "пост уже видели раньше — пропускаем без записи в БД"."""
    if post.external_id in repo.get_existing_external_ids(source.id):
        return None

    content_hash = compute_simhash(post.text) if post.text else None
    recent_hashes = repo.get_recent_content_hashes(source.id) if content_hash is not None else []

    raw_post = repo.create_raw_post(
        source_id=source.id,
        external_id=post.external_id,
        raw_text=post.text,
        content_hash=content_hash,
        fetched_at=post.published_at,
    )

    reason = _check_local_filters(post, filters, content_hash, recent_hashes)
    if reason is not None:
        return ProcessingOutcome(accepted=None, rejected=_reject(repo, raw_post.id, reason))

    try:
        classification = classify_post(
            llm_client, text=post.text, source=source.name, keywords=filters.whitelist_keywords
        )
    except (ClassificationError, LLMUnavailableError) as error:
        logger.warning("Классификация не удалась для raw_post %d: %s", raw_post.id, error)
        return ProcessingOutcome(
            accepted=None, rejected=_reject(repo, raw_post.id, "error_classification")
        )

    if not classification.is_news:
        reason = classification.reject_reason or "не новость по мнению LLM"
        return ProcessingOutcome(accepted=None, rejected=_reject(repo, raw_post.id, reason))

    matched_keywords = find_whitelisted_keywords(post.text, filters.whitelist_keywords)
    post_age_hours = _post_age_hours(post, max_post_age_hours)
    score = compute_score(
        news_value_score=classification.score,
        whitelist_matched=bool(matched_keywords),
        source_views=post.views,
        min_views=filters.min_views,
        post_age_hours=post_age_hours,
        max_post_age_hours=max_post_age_hours,
        source_priority=source.priority,
        weights=scoring_weights,
    )

    if score < filters.min_score:
        return ProcessingOutcome(
            accepted=None, rejected=_reject(repo, raw_post.id, "низкий скоринг", score=score)
        )

    rewritten_text = rewrite_post(
        llm_client, text=post.text, source=source.name, style=rewrite_config.style
    )
    headlines = generate_headlines(
        llm_client, text=rewritten_text, count=rewrite_config.headline_variants
    )
    headline = headlines[0] if headlines else None

    processed = repo.create_processed_post(
        raw_post_id=raw_post.id,
        score=score,
        category=classification.category,
        rewritten_text=rewritten_text,
        headline=headline,
        status="queued",
    )
    return ProcessingOutcome(accepted=processed, rejected=None)


def _check_local_filters(
    post: FetchedPost,
    filters: FiltersConfig,
    content_hash: int | None,
    recent_hashes: list[int],
) -> str | None:
    structural_reason = is_structural_non_news(post.post_type)
    if structural_reason is not None:
        return structural_reason

    blacklisted_word = find_blacklisted_word(post.text, filters.stop_words)
    if blacklisted_word is not None:
        return f"стоп-слово: {blacklisted_word}"

    if content_hash is not None:
        duplicate_hash = find_similar_hash(
            content_hash, recent_hashes, filters.duplicate_similarity_threshold
        )
        if duplicate_hash is not None:
            return "дубль по SimHash"

    return None


def _reject(repo: Repository, raw_post_id: int, reason: str, *, score: float = 0.0) -> RejectedPost:
    return repo.create_rejected_post(raw_post_id=raw_post_id, reason=reason, score=score)


def _post_age_hours(post: FetchedPost, max_post_age_hours: float) -> float:
    now = datetime.now(timezone.utc)
    published_at = post.published_at
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    age_hours = (now - published_at).total_seconds() / 3600
    return max(0.0, min(age_hours, max_post_age_hours))
