"""Правила работы с фото поста: рандом порядка, потолок кадров, режим плашки."""
import random
from unittest.mock import Mock

from app.config.loader import HeadlineCardConfig, ImagesConfig, UniquifyConfig, WatermarkConfig
from app.core.llm.client import LLMClient
from app.core.pipeline import _filter_watermarked_photos, _prepare_images


def _images_config() -> ImagesConfig:
    return ImagesConfig(
        providers_order=["source", "pexels"],
        count_per_post=1,
        target_aspect_ratio="4:5",
        uniquify=UniquifyConfig(enabled=False),
    )


def _watermark_config() -> WatermarkConfig:
    return WatermarkConfig(
        logo_path="assets/logo.png", position="top-right", opacity=65, margin_px=20
    )


def _run_prepare(monkeypatch, photos, **overrides) -> dict:
    import app.core.pipeline as pipeline_module

    monkeypatch.setattr(
        pipeline_module, "_filter_watermarked_photos", lambda *a, **k: list(photos)
    )
    captured: dict = {}

    def fake_prepare(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(pipeline_module, "prepare_images_for_post", fake_prepare)

    _prepare_images(
        Mock(spec=LLMClient),
        raw_post_id=1,
        post_media_urls=list(photos),
        rewritten_text="текст",
        images_config=_images_config(),
        watermark_config=_watermark_config(),
        headline_card_config=HeadlineCardConfig(),
        image_providers={},
        **overrides,
    )
    return captured


def test_max_images_caps_own_photos(monkeypatch):
    """ТЗ Кино 2026-08-10: «1 фото с текстом» — потолок бьёт и «все свои фото»."""
    captured = _run_prepare(monkeypatch, ["p1.jpg", "p2.jpg", "p3.jpg"], max_images_per_post=1)
    assert captured["count"] == 1


def test_without_cap_all_own_photos_are_used(monkeypatch):
    captured = _run_prepare(monkeypatch, ["p1.jpg", "p2.jpg", "p3.jpg"])
    assert captured["count"] == 3


def test_banner_mode_is_passed_to_the_filter(monkeypatch):
    import app.core.pipeline as pipeline_module

    seen: dict = {}

    def fake_filter(_client, urls, mode="drop"):
        seen["mode"] = mode
        return list(urls)

    monkeypatch.setattr(pipeline_module, "_filter_watermarked_photos", fake_filter)
    monkeypatch.setattr(pipeline_module, "prepare_images_for_post", lambda **kwargs: [])

    _prepare_images(
        Mock(spec=LLMClient),
        raw_post_id=1,
        post_media_urls=["p1.jpg"],
        rewritten_text="текст",
        images_config=_images_config(),
        watermark_config=_watermark_config(),
        headline_card_config=HeadlineCardConfig(),
        image_providers={},
        promo_banner_mode="restyle",
    )

    assert seen["mode"] == "restyle"


def test_restyle_mode_keeps_frame_with_reworked_banner(monkeypatch):
    import app.core.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "has_promo_banner", lambda path: True)
    monkeypatch.setattr(pipeline_module, "restyle_promo_banner", lambda path: "fixed.jpg")
    monkeypatch.setattr(pipeline_module, "locate_foreign_watermark", lambda *a: set())

    result = _filter_watermarked_photos(Mock(spec=LLMClient), ["banner.jpg"], "restyle")

    assert result == ["fixed.jpg"]


def test_restyle_failure_drops_the_frame(monkeypatch):
    """Владелец сформулировал прямо: «если так тяжело сделать, то лучше вообще
    этот кадр не брать»."""
    import app.core.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "has_promo_banner", lambda path: True)
    monkeypatch.setattr(pipeline_module, "restyle_promo_banner", lambda path: None)

    assert _filter_watermarked_photos(Mock(spec=LLMClient), ["banner.jpg"], "restyle") == []


def test_drop_mode_keeps_previous_behaviour(monkeypatch):
    import app.core.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "has_promo_banner", lambda path: True)
    restyle = Mock()
    monkeypatch.setattr(pipeline_module, "restyle_promo_banner", restyle)

    assert _filter_watermarked_photos(Mock(spec=LLMClient), ["banner.jpg"], "drop") == []
    restyle.assert_not_called()


def test_shuffle_changes_photo_order_deterministically_with_seeded_rng():
    """Проверяем сам инструмент перемешивания: с фиксированным зерном порядок
    воспроизводим, значит тест на пайплайне не будет мигать."""
    photos = ["p1.jpg", "p2.jpg", "p3.jpg", "p4.jpg"]
    first = list(photos)
    random.Random(7).shuffle(first)
    second = list(photos)
    random.Random(7).shuffle(second)

    assert first == second
    assert first != photos
