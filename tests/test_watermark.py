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
    )
    defaults.update(overrides)
    return WatermarkConfig(**defaults)


def test_crop_to_aspect_ratio_matches_target_when_within_crop_cap():
    # 1015x1000 (ratio 1.015) -> "1:1" needs ~1.5% width crop, under the 2% cap.
    image = Image.new("RGBA", (1015, 1000))
    cropped = crop_to_aspect_ratio(image, "1:1")
    assert cropped.width / cropped.height == pytest.approx(1.0, rel=0.02)


@pytest.mark.parametrize("ratio_key", ["4:5", "16:9", "9:16"])
def test_crop_to_aspect_ratio_never_crops_more_than_cap(ratio_key):
    # A square source is far from all of these ratios — cropping to the exact
    # target would cut ~20-44% of a dimension. Must clamp to <=2%, not force it.
    image = Image.new("RGBA", (1000, 1000))
    cropped = crop_to_aspect_ratio(image, ratio_key)
    assert cropped.width >= 1000 * 0.98 - 1
    assert cropped.height >= 1000 * 0.98 - 1


def test_crop_to_aspect_ratio_does_not_crop_when_already_target_ratio():
    image = Image.new("RGBA", (800, 1000))  # exactly 4:5
    cropped = crop_to_aspect_ratio(image, "4:5")
    assert (cropped.width, cropped.height) == (800, 1000)


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
    output_path = watermarker.apply(source_image_path, target_aspect_ratio="4:5", post_id=42)

    assert output_path.exists()
    result_image = Image.open(output_path)
    # 800x600 source (4:3) cropped toward "4:5" is capped at 2% max crop (see
    # MAX_CROP_FRACTION) rather than forced to exactly 0.8 — that would have cut
    # ~40% of the width. Expect the capped width (800 * 0.98 = 784), not the exact ratio.
    assert result_image.width == 784
    assert result_image.height == 600
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
