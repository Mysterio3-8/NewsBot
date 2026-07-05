import numpy as np
import pytest
from PIL import Image

from app.config.loader import HeadlineCardConfig
from app.core.images.headline_card import (
    HeadlineCardError,
    _wrap_text,
    apply_duotone,
    overlay_headline,
)


def test_apply_duotone_maps_black_and_white_extremes():
    image = Image.new("RGB", (10, 10))
    image.paste((0, 0, 0), (0, 0, 5, 10))
    image.paste((255, 255, 255), (5, 0, 10, 10))

    result = apply_duotone(image, dark=(10, 40, 30), light=(210, 255, 230))

    assert result.getpixel((1, 1))[:3] == (10, 40, 30)
    assert result.getpixel((8, 1))[:3] == (210, 255, 230)


def test_apply_duotone_removes_original_hue():
    image = Image.new("RGB", (10, 10), color=(255, 0, 0))  # чистый красный
    result = apply_duotone(image, dark=(0, 0, 0), light=(0, 255, 0))
    # Дуотон идёт через grayscale — чистый красный не должен остаться красным
    pixel = result.getpixel((0, 0))[:3]
    assert pixel[0] <= pixel[1]  # не доминирует красный канал


def test_wrap_text_splits_on_width():
    from PIL import ImageFont

    font = ImageFont.load_default()
    lines = _wrap_text("СЛОВО " * 20, font, max_width=100)
    assert len(lines) > 1
    assert all(line for line in lines)


def test_wrap_text_empty_string_returns_no_lines():
    from PIL import ImageFont

    assert _wrap_text("", ImageFont.load_default(), max_width=100) == []


def test_overlay_headline_raises_when_font_missing(tmp_path):
    config = HeadlineCardConfig(font_path="assets/fonts/does_not_exist.ttf")
    image = Image.new("RGBA", (200, 200))
    with pytest.raises(HeadlineCardError):
        overlay_headline(image, "Заголовок", config)


def test_overlay_headline_darkens_bottom_band_and_draws_text(monkeypatch, tmp_path):
    import app.core.images.headline_card as headline_card_module

    monkeypatch.setattr(headline_card_module, "PROJECT_ROOT", tmp_path)
    (tmp_path / "assets" / "fonts").mkdir(parents=True)
    import shutil
    from pathlib import Path

    real_font_src = Path("assets/fonts/DejaVuSans-Bold.ttf")
    shutil.copy(real_font_src, tmp_path / "assets" / "fonts" / "DejaVuSans-Bold.ttf")

    config = HeadlineCardConfig()
    image = Image.new("RGBA", (400, 400), color=(200, 200, 200, 255))
    before = np.asarray(image.copy())

    result = overlay_headline(image, "Тестовый заголовок дня", config)

    after = np.asarray(result)
    assert not np.array_equal(before, after)
    # Нижняя полоса должна стать темнее (градиент + текст)
    bottom_before = before[-10:, :, :3].mean()
    bottom_after = after[-10:, :, :3].mean()
    assert bottom_after < bottom_before
