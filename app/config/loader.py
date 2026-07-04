"""Загрузка и валидация config.yaml."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).parent / "config.yaml"


@dataclass(frozen=True)
class AppSection:
    name: str
    language: str
    timezone: str


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    host: str
    model: str
    temperature: float
    top_p: float
    timeout_seconds: int
    retries: int
    api_key_env: str = ""
    # Резервные модели того же провайдера: при отказе основной (напр. 429 у free-моделей
    # OpenRouter) клиент по очереди пробует их, пока одна не ответит. Пусто = без ротации.
    fallback_models: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MonitoringConfig:
    check_interval_minutes: int
    max_post_age_hours: int
    fetch_batch_size: int


@dataclass(frozen=True)
class FiltersConfig:
    min_score: int
    important_score_threshold: int
    duplicate_similarity_threshold: float
    min_views: int
    stop_words: list[str]
    required_keywords_boost: bool
    whitelist_keywords: list[str]
    blacklist_keywords: list[str]


@dataclass(frozen=True)
class ScoringWeights:
    news_value: float
    keyword_match: float
    source_views: float
    freshness: float
    source_priority: float


@dataclass(frozen=True)
class RewriteConfig:
    style: str
    max_length_chars: int
    headline_variants: int
    # LLM всё равно генерирует хэштеги (промпт не трогаем) — здесь просто решаем,
    # включать ли их в опубликованный текст. Выключено по запросу пользователя
    # 2026-07-03, легко включить обратно через config.yaml.
    include_hashtags: bool = False


@dataclass(frozen=True)
class UniquifyConfig:
    enabled: bool = False
    crop_percent: float = 1.0
    noise_sigma: float = 3.0


@dataclass(frozen=True)
class ImagesConfig:
    providers_order: list[str]
    count_per_post: int
    target_aspect_ratio: str
    uniquify: UniquifyConfig = field(default_factory=UniquifyConfig)


@dataclass(frozen=True)
class WatermarkConfig:
    logo_path: str
    position: str
    opacity: int
    margin_px: int
    channel_name_text: bool
    size_ratio: float = 0.2


@dataclass(frozen=True)
class ScheduleConfig:
    mode: str
    fixed_slots: list[str]
    interval_minutes: int
    max_posts_per_day: int
    # Жёсткий пол интервала между публикациями (защита от спама/бана) — соблюдается
    # на слое публикации, любой вызов publish_queued_post* его уважает.
    min_interval_minutes: int = 180
    # Случайный разброс времени публикации (±минут) — чтобы не постить робото-ровно.
    jitter_minutes: int = 15


@dataclass(frozen=True)
class PublishTargetConfig:
    enabled: bool
    token_env: str
    destination: str


@dataclass(frozen=True)
class PublishingConfig:
    telegram: PublishTargetConfig
    vk: PublishTargetConfig
    schedule: ScheduleConfig


@dataclass(frozen=True)
class LoggingConfig:
    level: str
    max_file_size_mb: int
    backup_count: int


@dataclass(frozen=True)
class FooterConfig:
    enabled: bool
    label: str
    telegram_url: str
    vk_url: str


@dataclass(frozen=True)
class AppConfig:
    app: AppSection
    llm: LLMConfig
    monitoring: MonitoringConfig
    filters: FiltersConfig
    scoring_weights: ScoringWeights
    rewrite: RewriteConfig
    images: ImagesConfig
    watermark: WatermarkConfig
    publishing: PublishingConfig
    logging: LoggingConfig
    footer: FooterConfig


class ConfigValidationError(ValueError):
    pass


def _validate(raw: dict) -> None:
    min_score = raw["filters"]["min_score"]
    if not 0 <= min_score <= 100:
        raise ConfigValidationError(f"filters.min_score должен быть 0-100, получено: {min_score}")

    important_score_threshold = raw["filters"]["important_score_threshold"]
    if not 0 <= important_score_threshold <= 100:
        raise ConfigValidationError(
            f"filters.important_score_threshold должен быть 0-100, "
            f"получено: {important_score_threshold}"
        )
    if important_score_threshold < min_score:
        raise ConfigValidationError(
            "filters.important_score_threshold не может быть меньше filters.min_score"
        )

    similarity = raw["filters"]["duplicate_similarity_threshold"]
    if not 0.0 <= similarity <= 1.0:
        raise ConfigValidationError(
            f"filters.duplicate_similarity_threshold должен быть 0.0-1.0, получено: {similarity}"
        )

    opacity = raw["watermark"]["opacity"]
    if not 0 <= opacity <= 100:
        raise ConfigValidationError(f"watermark.opacity должен быть 0-100, получено: {opacity}")

    weights = raw["scoring"]["weights"]
    total = sum(weights.values())
    if abs(total - 1.0) > 0.01:
        raise ConfigValidationError(f"scoring.weights должны в сумме давать 1.0, получено: {total}")


def update_config_section(path: Path | None, section: str, **fields) -> AppConfig:
    """Обновляет плоскую секцию config.yaml (filters/monitoring/watermark/rewrite/logging) и сохраняет."""
    config_path = path or CONFIG_PATH
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    raw[section].update(fields)
    _validate(raw)

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)

    return load_config(config_path)


def _build_images_config(raw_images: dict) -> ImagesConfig:
    raw = dict(raw_images)
    uniquify_raw = raw.pop("uniquify", None)
    uniquify = UniquifyConfig(**uniquify_raw) if uniquify_raw else UniquifyConfig()
    return ImagesConfig(**raw, uniquify=uniquify)


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"Файл конфигурации не найден: {config_path}")

    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    _validate(raw)

    publishing_raw = raw["publishing"]["targets"]
    publishing = PublishingConfig(
        telegram=PublishTargetConfig(
            enabled=publishing_raw["telegram"]["enabled"],
            token_env=publishing_raw["telegram"]["bot_token_env"],
            destination=publishing_raw["telegram"]["chat_id"],
        ),
        vk=PublishTargetConfig(
            enabled=publishing_raw["vk"]["enabled"],
            token_env=publishing_raw["vk"]["token_env"],
            destination=str(publishing_raw["vk"]["group_id"]),
        ),
        schedule=ScheduleConfig(**raw["publishing"]["schedule"]),
    )

    return AppConfig(
        app=AppSection(**raw["app"]),
        llm=LLMConfig(**raw["llm"]),
        monitoring=MonitoringConfig(**raw["monitoring"]),
        filters=FiltersConfig(**raw["filters"]),
        scoring_weights=ScoringWeights(**raw["scoring"]["weights"]),
        rewrite=RewriteConfig(**raw["rewrite"]),
        images=_build_images_config(raw["images"]),
        watermark=WatermarkConfig(**raw["watermark"]),
        publishing=publishing,
        logging=LoggingConfig(**raw["logging"]),
        footer=FooterConfig(**raw["footer"]),
    )
