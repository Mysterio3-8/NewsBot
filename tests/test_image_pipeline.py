from unittest.mock import Mock

from PIL import Image

from app.config.loader import WatermarkConfig
from app.core.images.image_pipeline import prepare_images_for_post
from app.core.images.providers.base import ImageResult
from app.core.images.watermark import Watermarker


def make_watermarker(tmp_path, monkeypatch) -> Watermarker:
    import app.core.images.watermark as watermark_module

    monkeypatch.setattr(watermark_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(watermark_module, "OUTPUT_DIR", tmp_path / "output")

    (tmp_path / "assets").mkdir()
    Image.new("RGBA", (100, 50), color=(255, 0, 0, 255)).save(tmp_path / "assets" / "logo.png")

    config = WatermarkConfig(
        logo_path="assets/logo.png",
        position="bottom-right",
        opacity=70,
        margin_px=10,
    )
    return Watermarker(config)


def test_prepare_images_uses_first_provider_with_results(tmp_path, monkeypatch):
    watermarker = make_watermarker(tmp_path, monkeypatch)
    source_image = tmp_path / "raw_source.jpg"
    Image.new("RGB", (800, 600), color="green").save(source_image)

    empty_provider = Mock()
    empty_provider.search.return_value = []

    found_provider = Mock()
    found_provider.search.return_value = [
        ImageResult(source_provider="source", local_path=source_image)
    ]

    results = prepare_images_for_post(
        providers_order=["source", "unsplash"],
        providers={"source": found_provider, "unsplash": empty_provider},
        query="новость",
        count=1,
        post_id=7,
        watermarker=watermarker,
        target_aspect_ratio="4:5",
        raw_output_dir=tmp_path / "raw",
    )

    assert len(results) == 1
    assert results[0].exists()
    found_provider.search.assert_called_once()


def test_prepare_images_falls_through_to_next_provider(tmp_path, monkeypatch):
    watermarker = make_watermarker(tmp_path, monkeypatch)
    source_image = tmp_path / "raw_source.jpg"
    Image.new("RGB", (800, 600), color="green").save(source_image)

    empty_provider = Mock()
    empty_provider.search.return_value = []
    fallback_provider = Mock()
    fallback_provider.search.return_value = [
        ImageResult(source_provider="unsplash", local_path=source_image)
    ]

    results = prepare_images_for_post(
        providers_order=["source", "unsplash"],
        providers={"source": empty_provider, "unsplash": fallback_provider},
        query="новость",
        count=1,
        post_id=8,
        watermarker=watermarker,
        target_aspect_ratio="1:1",
        raw_output_dir=tmp_path / "raw",
    )

    assert len(results) == 1
    fallback_provider.search.assert_called_once()


def test_prepare_images_applies_headline_only_to_first_photo(tmp_path):
    """По запросу пользователя 2026-07-05: "если фотки три, заголовок только на
    первом фото" — остальные получают только логотип (headline=None)."""
    watermarker = Mock(spec=Watermarker)
    watermarker.apply.side_effect = lambda path, **kwargs: path

    provider = Mock()
    provider.search.return_value = [
        ImageResult(source_provider="source", local_path=tmp_path / f"photo_{i}.jpg")
        for i in range(3)
    ]
    for i in range(3):
        Image.new("RGB", (10, 10)).save(tmp_path / f"photo_{i}.jpg")

    prepare_images_for_post(
        providers_order=["source"],
        providers={"source": provider},
        query="новость",
        count=3,
        post_id=10,
        watermarker=watermarker,
        target_aspect_ratio="4:5",
        raw_output_dir=tmp_path / "raw",
        headline="Главный заголовок",
    )

    headlines_passed = [call.kwargs["headline"] for call in watermarker.apply.call_args_list]
    assert headlines_passed == ["Главный заголовок", None, None]


def test_prepare_images_returns_empty_when_no_provider_finds_anything(tmp_path, monkeypatch):
    watermarker = make_watermarker(tmp_path, monkeypatch)
    empty_provider = Mock()
    empty_provider.search.return_value = []

    results = prepare_images_for_post(
        providers_order=["source"],
        providers={"source": empty_provider},
        query="новость",
        count=1,
        post_id=9,
        watermarker=watermarker,
        target_aspect_ratio="1:1",
        raw_output_dir=tmp_path / "raw",
    )

    assert results == []
