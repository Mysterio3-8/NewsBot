"""Оркестрация обработки поста: new → filtering → classified → rewritten → queued
(раздел 13.1 SPEC.md). Публикация — отдельный шаг по нажатию кнопки в UI.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from app.config.loader import (
    FiltersConfig,
    HeadlineCardConfig,
    ImagesConfig,
    RewriteConfig,
    ScoringWeights,
    WatermarkConfig,
)
from app.core.filtering.deduplication import compute_simhash, find_similar_hash
from app.core.filtering.rules import (
    find_blacklisted_word,
    find_whitelisted_keywords,
    is_structural_non_news,
)
from app.core.filtering.scoring import compute_score
from app.core.images.image_pipeline import prepare_images_for_post
from app.core.images.providers.base import ImageProvider
from app.core.images.providers.source_provider import SourceImageProvider
from app.core.images.resolver import resolve_to_local_file
from app.core.images.watermark import Watermarker, WatermarkError, crop_out_watermark_regions
from app.core.images.watermark_detector import locate_foreign_watermark
from app.core.llm.classifier import ClassificationError, classify_post
from app.core.llm.client import LLMClient, LLMUnavailableError
from app.core.llm.headline_generator import generate_headlines
from app.core.llm.image_query_generator import generate_image_query
from app.core.llm.movie_query_generator import generate_movie_search_query
from app.core.llm.rewriter import rewrite_post
from app.core.monitoring.models import FetchedPost
from app.core.video.watermark import VideoWatermarker, VideoWatermarkError
from app.db.models import ProcessedPost, RejectedPost, Source
from app.db.repository import Repository
from app.paths import OUTPUT_DIR

logger = logging.getLogger("monitoring")

# Потолок числа своих фото поста, идущих в публикацию (лимит медиагруппы TG/VK — 10).
MAX_SOURCE_PHOTOS = 10


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
    filters_enabled: bool = True,
    images_config: ImagesConfig | None = None,
    watermark_config: WatermarkConfig | None = None,
    headline_card_config: HeadlineCardConfig | None = None,
    image_providers: dict[str, ImageProvider] | None = None,
    image_query_mode: str = "generic",
    image_search_providers: list[str] | None = None,
) -> ProcessingOutcome | None:
    """None означает "пост уже видели раньше — пропускаем без записи в БД".
    filters_enabled=False (кино/мемы) — «лить всё»: пропускаем LLM-гейт новостей и
    min_score, оставляем только дедуп, чтобы не публиковать один пост дважды.
    images_config/watermark_config/image_providers опциональны — без них пост
    обрабатывается как раньше, но без картинок (обратная совместимость для тестов).
    image_query_mode="movie_title" (кино-канал) — вместо своих фото/стока ищем реальные
    кадры/постеры фильма через image_search_providers (см. ChannelSettings)."""
    if repo.has_external_id(source.id, post.external_id):
        return None

    content_hash = compute_simhash(post.text) if post.text else None
    recent_hashes = repo.get_recent_content_hashes(source.id) if content_hash is not None else []

    raw_post = repo.create_raw_post(
        source_id=source.id,
        external_id=post.external_id,
        raw_text=post.text,
        content_hash=content_hash,
        fetched_at=post.published_at,
        media=json.dumps(post.media_urls) if post.media_urls else None,
        video_path=post.video_path,
    )
    logger.info(
        "[пост %s] принят в обработку (источник=%s, медиа=%d фото%s, фильтры=%s)",
        post.external_id,
        source.name,
        len(post.media_urls),
        ", видео" if post.video_path else "",
        "вкл" if filters_enabled else "выкл",
    )

    if filters_enabled:
        reason = _check_local_filters(post, filters, content_hash, recent_hashes)
    else:
        reason = _check_duplicate_only(post, filters, content_hash, recent_hashes)
    if reason is not None:
        return ProcessingOutcome(accepted=None, rejected=_reject(repo, raw_post.id, reason))

    if filters_enabled:
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
        category = classification.category
    else:
        # «Лить всё»: без LLM-гейта новостей и порога скоринга. Фиксированный проходной
        # скор (публикация в headless идёт по created_at, не по score).
        score = 100.0
        category = None

    rewritten_text = rewrite_post(
        llm_client,
        text=post.text,
        source=source.name,
        style=rewrite_config.style,
        # Объём рерайта ≈ как у оригинала (запрос пользователя 2026-07-09: "по объёму
        # примерно по оригиналу, не сокращая и не добавляя"). Потолок = длина исходника:
        # не заставляет ужимать (смысл сохраняется) и не даёт раздувать вдвое (раньше
        # был max(900, len) — LLM растягивал короткие посты до 900). Промпт держит длину.
        max_length=len(post.text),
        include_hashtags=rewrite_config.include_hashtags,
    )
    logger.info(
        "[пост %s] рерайт готов (%d → %d символов)",
        post.external_id,
        len(post.text),
        len(rewritten_text),
    )
    headlines = generate_headlines(
        llm_client,
        text=rewritten_text,
        style=rewrite_config.style,
        count=rewrite_config.headline_variants,
    )
    headline = headlines[0] if headlines else None

    image_paths = _prepare_images(
        llm_client,
        raw_post_id=raw_post.id,
        post_media_urls=post.media_urls,
        rewritten_text=rewritten_text,
        headline=headline,
        images_config=images_config,
        watermark_config=watermark_config,
        headline_card_config=headline_card_config,
        image_providers=image_providers,
        image_query_mode=image_query_mode,
        image_search_providers=image_search_providers,
    )
    video_path = _prepare_video(
        raw_post_id=raw_post.id,
        post_video_path=post.video_path,
        watermark_config=watermark_config,
        images_config=images_config,
    )
    logger.info(
        "[пост %s] медиа подготовлено (изображений=%s, видео=%s)",
        post.external_id,
        len(image_paths) if image_paths else 0,
        "да" if video_path else "нет",
    )

    processed = repo.create_processed_post(
        raw_post_id=raw_post.id,
        score=score,
        category=category,
        rewritten_text=rewritten_text,
        headline=headline,
        image_paths=image_paths,
        video_path=video_path,
        status="queued",
    )
    logger.info(
        "[пост %s] поставлен в очередь публикации (processed_post=%d, score=%.1f)",
        post.external_id,
        processed.id,
        score,
    )
    return ProcessingOutcome(accepted=processed, rejected=None)


def _prepare_images(
    llm_client: LLMClient,
    *,
    raw_post_id: int,
    post_media_urls: list[str],
    rewritten_text: str,
    headline: str | None = None,
    images_config: ImagesConfig | None,
    watermark_config: WatermarkConfig | None,
    headline_card_config: HeadlineCardConfig | None = None,
    image_providers: dict[str, ImageProvider] | None,
    image_query_mode: str = "generic",
    image_search_providers: list[str] | None = None,
) -> list[str] | None:
    """Своё фото поста (SourceImageProvider) в приоритете — если его нет (или все
    отфильтрованы как чужой водяной знак, см. _filter_watermarked_photos), для
    стока нужен поисковый запрос (отдельный LLM-вызов), генерируем только когда
    он реально нужен, чтобы не тратить лимит Groq впустую."""
    if images_config is None or watermark_config is None:
        return None

    # Кино-канал (image_query_mode="movie_title"): не берём фото источника и не идём
    # в обычный сток — ищем РЕАЛЬНЫЕ кадры/постеры конкретного фильма по названию,
    # извлечённому LLM (см. ChannelSettings.image_query_mode, MULTICHANNEL.md срез 2.5).
    if image_query_mode == "movie_title":
        return _prepare_movie_images(
            llm_client,
            raw_post_id=raw_post_id,
            rewritten_text=rewritten_text,
            headline=headline,
            images_config=images_config,
            watermark_config=watermark_config,
            headline_card_config=headline_card_config,
            image_providers=image_providers,
            providers_order=image_search_providers or [],
        )

    # Лёгкий режим (keep_original): свои медиа поста отдаём как есть — без монтажа/
    # вотермарка/уникализации. Но если своего фото НЕТ, пост без картинки читается плохо
    # (запрос пользователя 2026-07-09) — берём сток (Pexels и др.) по запросу из текста,
    # тоже без обработки (лёгкий режим консистентен).
    if images_config.keep_original:
        originals = post_media_urls[:MAX_SOURCE_PHOTOS]
        if originals:
            return [str(p) for p in originals]
        return _fetch_stock_plain(
            llm_client, rewritten_text, raw_post_id, images_config, image_providers
        )

    own_photos = _filter_watermarked_photos(llm_client, post_media_urls)

    providers: dict[str, ImageProvider] = dict(image_providers or {})
    if own_photos:
        providers["source"] = SourceImageProvider(own_photos)

    if not any(name in providers for name in images_config.providers_order):
        return None

    query = "" if own_photos else _safe_image_query(llm_client, rewritten_text)

    # Свои фото поста берём ВСЕ (запрос пользователя 2026-07-05: "медиа качай все...
    # на первое фото заголовок, на остальные монтаж") — до разумного потолка в 10
    # (лимит медиагруппы TG/VK). Сток-фолбэк (нет своих фото) — только count_per_post
    # (по умолчанию 1, "бери только одно, как в оригинале").
    count = min(len(own_photos), MAX_SOURCE_PHOTOS) if own_photos else images_config.count_per_post

    try:
        watermarker = Watermarker(watermark_config, images_config.uniquify, headline_card_config)
        image_paths = prepare_images_for_post(
            providers_order=images_config.providers_order,
            providers=providers,
            query=query,
            count=count,
            post_id=raw_post_id,
            headline=headline,
            watermarker=watermarker,
            target_aspect_ratio=images_config.target_aspect_ratio,
            raw_output_dir=OUTPUT_DIR / "raw" / str(raw_post_id),
        )
    except WatermarkError as error:
        logger.warning("Watermark не удался для raw_post %d: %s", raw_post_id, error)
        return None

    return [str(p) for p in image_paths] or None


def _fetch_stock_plain(
    llm_client: LLMClient,
    rewritten_text: str,
    raw_post_id: int,
    images_config: ImagesConfig,
    image_providers: dict[str, ImageProvider] | None,
) -> list[str] | None:
    """Сток-фото для лёгкого режима, когда у поста нет своего: скачиваем по запросу из
    текста и отдаём БЕЗ вотермарка/монтажа (в отличие от prepare_images_for_post, который
    всегда применяет watermarker). Провайдеры — из providers_order, кроме source."""
    providers = dict(image_providers or {})
    stock_names = [n for n in images_config.providers_order if n != "source" and n in providers]
    if not stock_names:
        return None

    query = _safe_image_query(llm_client, rewritten_text)
    if not query:
        return None

    output_dir = OUTPUT_DIR / "raw" / str(raw_post_id)
    for name in stock_names:
        results = providers[name].search(query, images_config.count_per_post)
        if not results:
            continue
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = [
            resolve_to_local_file(result, output_dir / f"stock_{index}.jpg")
            for index, result in enumerate(results)
        ]
        return [str(p) for p in paths]
    return None


def _prepare_movie_images(
    llm_client: LLMClient,
    *,
    raw_post_id: int,
    rewritten_text: str,
    headline: str | None,
    images_config: ImagesConfig,
    watermark_config: WatermarkConfig,
    headline_card_config: HeadlineCardConfig | None,
    image_providers: dict[str, ImageProvider] | None,
    providers_order: list[str],
) -> list[str] | None:
    """Кино-канал: ищет реальные кадры/постеры конкретного фильма (Google Custom Search,
    обычно) вместо оригинальных фото источника или нейтрального стока. Watermark (лого)
    накладывается как обычно через prepare_images_for_post; коллаж/плашка появятся, когда
    включат headline_card под кино-стиль — код монтажа общий, не дублируется здесь."""
    providers = dict(image_providers or {})
    search_names = [name for name in providers_order if name in providers]
    if not search_names:
        logger.warning(
            "raw_post %d: не задан провайдер поиска кадров (image_providers_order канала)",
            raw_post_id,
        )
        return None

    query = _safe_movie_query(llm_client, rewritten_text)
    if not query:
        return None

    try:
        watermarker = Watermarker(watermark_config, images_config.uniquify, headline_card_config)
        image_paths = prepare_images_for_post(
            providers_order=search_names,
            providers=providers,
            query=query,
            count=images_config.count_per_post,
            post_id=raw_post_id,
            headline=headline,
            watermarker=watermarker,
            target_aspect_ratio=images_config.target_aspect_ratio,
            raw_output_dir=OUTPUT_DIR / "raw" / str(raw_post_id),
        )
    except WatermarkError as error:
        logger.warning("Watermark не удался для кадров фильма raw_post %d: %s", raw_post_id, error)
        return None

    return [str(p) for p in image_paths] or None


def _safe_movie_query(llm_client: LLMClient, rewritten_text: str) -> str:
    try:
        return generate_movie_search_query(llm_client, text=rewritten_text)
    except LLMUnavailableError:
        return ""


def _filter_watermarked_photos(llm_client: LLMClient, media_urls: list[str]) -> list[str]:
    """Держится за ОРИГИНАЛЬНОЕ фото поста, а не подменяет его сток-фото, когда
    это возможно (запрос пользователя 2026-07-05: "находить оригинальное фото,
    только без чужого монтажа и вотермарков"). Если чужой знак — у верхнего и/или
    нижнего края, обрезаем эту полосу и используем очищенную версию того же фото.
    Только если знак по центру/на самом сюжете (обрезкой не убрать) — фото
    отбрасывается совсем, пайплайн переходит на сток-фолбэк, как раньше.
    И TG (TelegramFetcher), и VK (VKFetcher) скачивают фото на диск ДО этого шага,
    так что media_urls здесь — всегда локальные пути; http(s)-ветка ниже — защита
    на случай прямого вызова с ещё не скачанным URL, а не штатный путь для VK."""
    kept: list[str] = []
    for item in media_urls:
        if item.startswith("http://") or item.startswith("https://"):
            kept.append(item)
            continue

        regions = locate_foreign_watermark(llm_client, Path(item))
        if regions is None:
            logger.info("Фото %s: чужой знак нельзя убрать обрезкой, пропускаю", item)
            continue
        if not regions:
            kept.append(item)
            continue

        cleaned_path = _crop_out_watermark(item, regions)
        logger.info(
            "Фото %s: обрезал чужой знак (%s), использую очищенную версию",
            item, ",".join(sorted(regions)),
        )
        kept.append(str(cleaned_path))
    return kept


def _crop_out_watermark(image_path: str, regions: set[str]) -> Path:
    path = Path(image_path)
    image = Image.open(path).convert("RGBA")
    cleaned = crop_out_watermark_regions(image, regions)
    cleaned_path = path.with_name(f"{path.stem}_clean{path.suffix}")
    cleaned.convert("RGB").save(cleaned_path)
    return cleaned_path


def _prepare_video(
    *,
    raw_post_id: int,
    post_video_path: str | None,
    watermark_config: WatermarkConfig | None,
    images_config: ImagesConfig | None,
) -> str | None:
    """Своё видео поста — watermark + встроенная уникализация через ffmpeg
    (app/core/video/watermark.py), в отличие от фото не участвует в сток-фолбэке —
    сток-видео не подключено. Уникализация видео управляется тем же тумблером
    images_config.uniquify.enabled, что и уникализация фото — единая настройка проекта."""
    if not post_video_path or watermark_config is None:
        return None

    # Видео без вотермарка/монтажа (запрос пользователя 2026-07-05) — публикуем как есть.
    if not watermark_config.video_enabled:
        return post_video_path

    uniquify_enabled = images_config is not None and images_config.uniquify.enabled

    try:
        watermarker = VideoWatermarker(watermark_config, uniquify_enabled=uniquify_enabled)
        output_path = watermarker.apply(Path(post_video_path), post_id=raw_post_id)
    except VideoWatermarkError as error:
        logger.warning("Video watermark не удался для raw_post %d: %s", raw_post_id, error)
        return None

    return str(output_path)


def _safe_image_query(llm_client: LLMClient, rewritten_text: str) -> str:
    try:
        return generate_image_query(llm_client, text=rewritten_text)
    except LLMUnavailableError:
        return ""


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


def _check_duplicate_only(
    post: FetchedPost,
    filters: FiltersConfig,
    content_hash: int | None,
    recent_hashes: list[int],
) -> str | None:
    """Режим «лить всё» (filters_enabled=False): пропускаем стоп-слова и LLM-гейт, но
    дедуп по SimHash и структурную проверку оставляем — чтобы один и тот же пост не
    ушёл в канал дважды и не проскочил не-текстовый мусор."""
    structural_reason = is_structural_non_news(post.post_type)
    if structural_reason is not None:
        return structural_reason

    if content_hash is not None:
        duplicate_hash = find_similar_hash(
            content_hash, recent_hashes, filters.duplicate_similarity_threshold
        )
        if duplicate_hash is not None:
            return "дубль по SimHash"

    return None


def _reject(repo: Repository, raw_post_id: int, reason: str, *, score: float = 0.0) -> RejectedPost:
    """Логируем каждый отказ (раньше это было видно только в БД — приходилось
    руками лезть в rejected_posts, чтобы понять, почему пайплайн ничего не публикует)."""
    logger.info("raw_post %d отклонён: %s (score=%.2f)", raw_post_id, reason, score)
    return repo.create_rejected_post(raw_post_id=raw_post_id, reason=reason, score=score)


def _post_age_hours(post: FetchedPost, max_post_age_hours: float) -> float:
    now = datetime.now(timezone.utc)
    published_at = post.published_at
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    age_hours = (now - published_at).total_seconds() / 3600
    return max(0.0, min(age_hours, max_post_age_hours))
