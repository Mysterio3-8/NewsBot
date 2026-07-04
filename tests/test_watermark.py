import pytest
from PIL import Image

from app.config.loader import UniquifyConfig, WatermarkConfig
from app.core.images.watermark import Watermarker, WatermarkError, crop_to_aspect_ratio


def make_config(**overrides) -> WatermarkConfig:
    defaults = dict(
        logo_path="assets/test_logo.png",
        position="bottom-right",
        opacity=70,
        margin_px=10,
        channel_name_text=True,
    )
    defaults.update(overrides)
    return WatermarkConfig(**defaults)


@pytest.mark.parametrize(
    "ratio_key,expected_ratio",
    [("1:1", 1.0), ("4:5", 0.8), ("16:9", 16 / 9), ("9:16", 9 / 16)],
)
def test_crop_to_aspect_ratio_produces_correct_ratio(ratio_key, expected_ratio):
    image = Image.new("RGBA", (1000, 1000))
    cropped = crop_to_aspect_ratio(image, ratio_key)
    assert cropped.width / cropped.height == pytest.approx(expected_ratio, rel=0.02)


def test_crop_to_aspect_ratio_rejects_unknown_key():
    image = Image.new("RGBA", (100, 100))
    with pytest.raises(WatermarkError):
        crop_to_aspect_ratio(image, "3:3:3")


def test_watermarker_raises_when_logo_missing(tmp_path):
    source_image_path = tmp_path / "source.jpg"
    Image.new("RGB", (800, 600), color="blue").save(source_image_path)

    watermarker = Watermarker(make_config(logo_path="assets/does_not_exist.png"))

    with pytest.raises(WatermarkError):
        watermarker.apply(source_image_path, target_aspect_ratio="4:5", post_id=1)


def test_watermarker_applies_logo_and_saves_output(tmp_path, monkeypatch):
    import app.core.images.watermark as watermark_module

    monkeypatch.setattr(watermark_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(watermark_module, "OUTPUT_DIR", tmp_path / "output")

    (tmp_path / "assets").mkdir()
    logo_path = tmp_path / "assets" / "logo.png"
    Image.new("RGBA", (200, 100), color=(255, 0, 0, 255)).save(logo_path)

    source_image_path = tmp_path / "source.jpg"
    Image.new("RGB", (800, 600), color="blue").save(source_image_path)

    watermarker = Watermarker(make_config(logo_path="assets/logo.png"))
    output_path = watermarker.apply(
        source_image_path, target_aspect_ratio="4:5", post_id=42, channel_name="Мой канал"
    )

    assert output_path.exists()
    result_image = Image.open(output_path)
    assert result_image.width / result_image.height == pytest.approx(0.8, rel=0.02)
    assert output_path.parent == tmp_path / "output" / "images" / "42"


def test_watermarker_output_has_no_exif_metadata(tmp_path, monkeypatch):
    import app.core.images.watermark as watermark_module

    monkeypatch.setattr(watermark_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(watermark_module, "OUTPUT_DIR", tmp_path / "output")

    (tmp_path / "assets").mkdir()
    Image.new("RGBA", (200, 100), color=(255, 0, 0, 255)).save(tmp_path / "assets" / "logo.png")

    from PIL import Image as PILImage

    exif = PILImage.Exif()
    exif[0x010F] = "Секретный источник"  # Make
    source_image_path = tmp_path / "source.jpg"
    PILImage.new("RGB", (800, 600), color="blue").save(source_image_path, exif=exif)

    watermarker = Watermarker(make_config(logo_path="assets/logo.png"))
    output_path = watermarker.apply(source_image_path, target_aspect_ratio="4:5", post_id=1)

    assert not dict(Image.open(output_path).getexif())


def test_watermarker_uniquify_changes_pixels_vs_disabled(tmp_path, monkeypatch):
    import app.core.images.watermark as watermark_module

    monkeypatch.setattr(watermark_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(watermark_module, "OUTPUT_DIR", tmp_path / "output")

    (tmp_path / "assets").mkdir()
    Image.new("RGBA", (200, 100), color=(255, 0, 0, 255)).save(tmp_path / "assets" / "logo.png")
    source_image_path = tmp_path / "source.jpg"
    Image.effect_noise((800, 600), 40).convert("RGB").save(source_image_path)

    import numpy as np

    plain = Watermarker(make_config(logo_path="assets/logo.png"), UniquifyConfig(enabled=False))
    plain_out = plain.apply(source_image_path, target_aspect_ratio="4:5", post_id=1)

    uniq = Watermarker(
        make_config(logo_path="assets/logo.png"),
        UniquifyConfig(enabled=True, crop_percent=1.0, noise_sigma=3.0),
    )
    uniq_out = uniq.apply(source_image_path, target_aspect_ratio="4:5", post_id=2)

    assert not np.array_equal(
        np.asarray(Image.open(plain_out)), np.asarray(Image.open(uniq_out))
    )
