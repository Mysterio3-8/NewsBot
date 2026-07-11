import pytest

from app.config.loader import ConfigValidationError, load_config


def test_load_config_returns_typed_config():
    config = load_config()

    assert config.app.name == "AI News Rewriter"
    assert config.llm.model == "llama-3.1-8b-instant"
    assert config.filters.min_score == 55


def test_load_config_reads_headline_card_section():
    config = load_config()

    # Монтаж выключен в лёгком режиме (2026-07-07), но секция всё равно читается.
    assert config.headline_card.enabled is False
    assert config.headline_card.corner_fade_corners == ["bottom-left", "top-right"]


def test_load_config_reads_images_keep_original():
    """2026-07-11: вотермарк снова включён (было true — "лёгкий режим" 2026-07-07,
    отменено по жалобе пользователя на фото без лого)."""
    config = load_config()

    assert config.images.keep_original is False


def test_load_config_reads_antiban_section():
    """ТЗ 2026-07-10: жёсткий лимит на личный VK-токен, настраиваемый без правки кода."""
    config = load_config()

    assert config.antiban.vk_personal_token_cooldown_seconds == 600


def test_load_config_defaults_antiban_when_section_missing(tmp_path):
    bad_config = tmp_path / "config.yaml"
    bad_config.write_text(
        """
app: {name: x, language: ru, timezone: UTC}
llm: {provider: ollama, host: h, model: m, temperature: 0.7, top_p: 0.9, timeout_seconds: 60, retries: 1}
monitoring: {check_interval_minutes: 240, max_post_age_hours: 24, fetch_batch_size: 50}
filters: {min_score: 50, important_score_threshold: 65, duplicate_similarity_threshold: 0.85, min_views: 500, stop_words: [], required_keywords_boost: true, whitelist_keywords: [], blacklist_keywords: []}
scoring: {weights: {news_value: 0.35, keyword_match: 0.25, source_views: 0.20, freshness: 0.10, source_priority: 0.10}}
rewrite: {style: viral, max_length_chars: 900, headline_variants: 3}
images: {providers_order: [source], count_per_post: 3, target_aspect_ratio: "4:5"}
watermark: {logo_path: x, position: bottom-right, opacity: 70, margin_px: 24}
publishing:
  targets:
    telegram: {enabled: true, bot_token_env: TG_BOT_TOKEN, chat_id: "@x"}
    vk: {enabled: true, token_env: VK_GROUP_TOKEN, group_id: 0}
  schedule: {mode: fixed_slots, fixed_slots: [], interval_minutes: 40, max_posts_per_day: 12}
logging: {level: INFO, max_file_size_mb: 10, backup_count: 5}
footer: {enabled: false, label: x, telegram_url: "", vk_url: ""}
""",
        encoding="utf-8",
    )

    config = load_config(bad_config)

    assert config.antiban.vk_personal_token_cooldown_seconds == 600


def test_load_config_defaults_headline_card_when_section_missing(tmp_path):
    bad_config = tmp_path / "config.yaml"
    bad_config.write_text(
        """
app: {name: x, language: ru, timezone: UTC}
llm: {provider: ollama, host: h, model: m, temperature: 0.7, top_p: 0.9, timeout_seconds: 60, retries: 1}
monitoring: {check_interval_minutes: 240, max_post_age_hours: 24, fetch_batch_size: 50}
filters: {min_score: 50, important_score_threshold: 65, duplicate_similarity_threshold: 0.85, min_views: 500, stop_words: [], required_keywords_boost: true, whitelist_keywords: [], blacklist_keywords: []}
scoring: {weights: {news_value: 0.35, keyword_match: 0.25, source_views: 0.20, freshness: 0.10, source_priority: 0.10}}
rewrite: {style: viral, max_length_chars: 900, headline_variants: 3}
images: {providers_order: [source], count_per_post: 3, target_aspect_ratio: "4:5"}
watermark: {logo_path: x, position: bottom-right, opacity: 70, margin_px: 24}
publishing:
  targets:
    telegram: {enabled: true, bot_token_env: TG_BOT_TOKEN, chat_id: "@x"}
    vk: {enabled: true, token_env: VK_GROUP_TOKEN, group_id: 0}
  schedule: {mode: fixed_slots, fixed_slots: [], interval_minutes: 40, max_posts_per_day: 12}
logging: {level: INFO, max_file_size_mb: 10, backup_count: 5}
footer: {enabled: false, label: x, telegram_url: "", vk_url: ""}
""",
        encoding="utf-8",
    )

    config = load_config(bad_config)

    assert config.headline_card.enabled is False


def test_load_config_missing_file_raises(tmp_path):
    missing_path = tmp_path / "does_not_exist.yaml"

    with pytest.raises(FileNotFoundError):
        load_config(missing_path)


def test_load_config_rejects_invalid_min_score(tmp_path):
    bad_config = tmp_path / "config.yaml"
    bad_config.write_text(
        """
app: {name: x, language: ru, timezone: UTC}
llm: {provider: ollama, host: h, model: m, temperature: 0.7, top_p: 0.9, timeout_seconds: 60, retries: 1}
monitoring: {check_interval_minutes: 240, max_post_age_hours: 24, fetch_batch_size: 50}
filters: {min_score: 150, duplicate_similarity_threshold: 0.85, min_views: 500, stop_words: [], required_keywords_boost: true, whitelist_keywords: [], blacklist_keywords: []}
scoring: {weights: {news_value: 0.35, keyword_match: 0.25, source_views: 0.20, freshness: 0.10, source_priority: 0.10}}
rewrite: {style: viral, max_length_chars: 900, headline_variants: 3}
images: {providers_order: [source], count_per_post: 3, target_aspect_ratio: "4:5"}
watermark: {logo_path: x, position: bottom-right, opacity: 70, margin_px: 24}
publishing:
  targets:
    telegram: {enabled: true, bot_token_env: TG_BOT_TOKEN, chat_id: "@x"}
    vk: {enabled: true, token_env: VK_GROUP_TOKEN, group_id: 0}
  schedule: {mode: fixed_slots, fixed_slots: [], interval_minutes: 40, max_posts_per_day: 12}
logging: {level: INFO, max_file_size_mb: 10, backup_count: 5}
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigValidationError):
        load_config(bad_config)
