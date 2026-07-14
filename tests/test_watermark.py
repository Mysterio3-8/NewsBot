import pytest
from PIL import Image

from app.config.loader import HeadlineCardConfig, UniquifyConfig, WatermarkConfig
from app.core.images.watermark import (
    Watermarker,
    WatermarkError,
    crop_out_watermark_regions,
    crop_to_aspect_ratio,
    fit_to_aspect_ratio,
)


def test_fit_to_aspect_ratio_pads_wide_image_to_square_without_cropping():
    """Широкое фото приводится к 1:1 БЕЗ обрезки: ширина сохраняется, высота
    дорастает размытыми полями. Контент не теряется (жалоба "VK фотка обрубленная")."""
    image = Image.new("RGBA", (1000, 500), color=(0, 0, 255, 255))
    result = fit_to_aspect_ratio(image, "1:1")
    assert result.size == (1000, 1000)  # квадрат, ширина не тронута


def test_fit_to_aspect_ratio_returns_unchanged_when_already_target():
    image = Image.new("RGBA", (600, 600))
    result = fit_to_aspect_ratio(image, "1:1")
    assert result.size == (600, 600)


def test_fit_to_aspect_ratio_keeps_original_photo_centered():
    """Оригинал вписан по центру целиком — центральный пиксель остаётся исходным."""
    import numpy as np

    image = Image.new("RGBA", (1000, 400), color=(0, 0, 255, 255))
    result = fit_to_aspect_ratio(image, "1:1")
    arr = np.asarray(result.convert("RGB"))
    center = arr[500, 500]
    assert center[2] > center[0] and center[2] > center[1]  # синий оригинал в центре


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
    # 800x600 (4:3) приводится к "4:5" (0.8) через fit_to_aspect_ratio: не обрезкой,
    # а размытыми полями сверху/снизу — контент сохраняется полностью. Ширина остаётся
    # 800, высота растёт до 800/0.8 = 1000.
    assert result_image.width == 800
    assert result_image.height == 1000
    assert output_path.parent == tmp_path / "output" / "images" / "42"


def test_watermarker_crop_mode_crops_instead_of_blur_padding(tmp_path, monkeypatch):
    """aspect_mode='crop' (запрос 2026-07-11 «1:1 без блюра»): широкое фото режется
    к соотношению, а не вписывается на размытую подложку. Ключевой признак: высота
    исходника СОХРАНЯЕТСЯ (blur-fill бы её увеличил, добавив поля)."""
    import app.core.images.watermark as watermark_module

    monkeypatch.setattr(watermark_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(watermark_module, "OUTPUT_DIR", tmp_path / "output")

    (tmp_path / "assets").mkdir()
    Image.new("RGBA", (200, 100), color=(255, 0, 0, 255)).save(tmp_path / "assets" / "logo.png")

    source_image_path = tmp_path / "source.jpg"
    Image.new("RGB", (800, 600), color="blue").save(source_image_path)

    watermarker = Watermarker(make_config(logo_path="assets/logo.png"), aspect_mode="crop")
    output_path = watermarker.apply(source_image_path, target_aspect_ratio="1:1", post_id=7)

    result = Image.open(output_path)
    assert result.height == 600  # высота не выросла — значит НЕ blur-подложка
    assert result.width < 800    # ширина урезана к квадрату (в пределах cap)


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


def test_watermarker_applies_headline_card_when_enabled_and_headline_given(tmp_path, monkeypatch):
    import shutil

    import app.core.images.watermark as watermark_module

    monkeypatch.setattr(watermark_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(watermark_module, "OUTPUT_DIR", tmp_path / "output")

    (tmp_path / "assets" / "fonts").mkdir(parents=True)
    Image.new("RGBA", (200, 100), color=(255, 0, 0, 255)).save(tmp_path / "assets" / "logo.png")
    shutil.copy(
        "assets/fonts/DejaVuSans-Bold.ttf", tmp_path / "assets" / "fonts" / "DejaVuSans-Bold.ttf"
    )
    source_image_path = tmp_path / "source.jpg"
    Image.new("RGB", (800, 600), color="blue").save(source_image_path)

    import numpy as np

    plain = Watermarker(make_config(logo_path="assets/logo.png"))
    plain_out = plain.apply(source_image_path, target_aspect_ratio="4:5", post_id=1)

    with_card = Watermarker(
        make_config(logo_path="assets/logo.png"),
        headline_card_config=HeadlineCardConfig(enabled=True),
    )
    card_out = with_card.apply(
        source_image_path, target_aspect_ratio="4:5", post_id=2, headline="Тестовый заголовок"
    )

    assert not np.array_equal(
        np.asarray(Image.open(plain_out)), np.asarray(Image.open(card_out))
    )


def test_watermarker_applies_corner_fade_but_no_headline_when_headline_none(tmp_path, monkeypatch):
    """headline=None (не первое фото) — зелёный фейд по углам применяется, а заголовок
    НЕТ. Значит шрифт (которого тут нет на диске) не нужен и падать не должно."""
    import app.core.images.watermark as watermark_module

    monkeypatch.setattr(watermark_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(watermark_module, "OUTPUT_DIR", tmp_path / "output")

    (tmp_path / "assets").mkdir()
    Image.new("RGBA", (200, 100), color=(255, 0, 0, 255)).save(tmp_path / "assets" / "logo.png")
    source_image_path = tmp_path / "source.jpg"
    Image.new("RGB", (800, 600), color="blue").save(source_image_path)

    watermarker = Watermarker(
        make_config(logo_path="assets/logo.png"),
        headline_card_config=HeadlineCardConfig(enabled=True),
    )
    output_path = watermarker.apply(source_image_path, target_aspect_ratio="4:5", post_id=1)
    assert output_path.exists()


@pytest.mark.parametrize(
    "regions,expected_size",
    [
        ({"top"}, (400, 320)),
        ({"bottom"}, (400, 320)),
        ({"top", "bottom"}, (400, 240)),
        (set(), (400, 400)),
    ],
)
def test_crop_out_watermark_regions_removes_correct_edges(regions, expected_size):
    image = Image.new("RGBA", (400, 400))
    cropped = crop_out_watermark_regions(image, regions, fraction=0.2)
    assert cropped.size == expected_size


def test_crop_out_watermark_regions_never_crops_entire_image():
    image = Image.new("RGBA", (400, 10))
    cropped = crop_out_watermark_regions(image, {"top", "bottom"}, fraction=0.6)
    assert cropped.size == (400, 10)  # 0.6+0.6 >= 1.0 — защита сработала, не режем


def test_watermarker_center_keeps_original_colors_no_full_tint(tmp_path, monkeypatch):
    """Фейд — только по углам, центр фото остаётся оригинальным (не как отвергнутый
    дуотон, красивший весь кадр). Проверяем: центр синего фото остаётся синим."""
    import shutil

    import numpy as np

    import app.core.images.watermark as watermark_module

    monkeypatch.setattr(watermark_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(watermark_module, "OUTPUT_DIR", tmp_path / "output")

    (tmp_path / "assets" / "fonts").mkdir(parents=True)
    Image.new("RGBA", (200, 100), color=(255, 0, 0, 255)).save(tmp_path / "assets" / "logo.png")
    shutil.copy(
        "assets/fonts/DejaVuSans-Bold.ttf", tmp_path / "assets" / "fonts" / "DejaVuSans-Bold.ttf"
    )
    source_image_path = tmp_path / "source.jpg"
    Image.new("RGB", (800, 600), color="blue").save(source_image_path)

    watermarker = Watermarker(
        make_config(logo_path="assets/logo.png"),
        headline_card_config=HeadlineCardConfig(enabled=True),
    )
    output_path = watermarker.apply(
        source_image_path, target_aspect_ratio="4:5", post_id=1, headline="Заголовок"
    )

    result = np.asarray(Image.open(output_path).convert("RGB"))
    h, w = result.shape[:2]
    center_pixel = result[h // 3, w // 2]  # верхняя треть по центру — вдали от углов и полосы
    assert center_pixel[2] > center_pixel[1]  # синий доминирует, центр не позеленел
