import numpy as np
import pytest
from PIL import Image

from app.config.loader import HeadlineCardConfig
from app.core.images.headline_card import (
    HeadlineCardError,
    _wrap_text,
    apply_corner_fade,
    overlay_headline,
)


def test_apply_corner_fade_tints_specified_corners_green():
    image = Image.new("RGBA", (100, 100), color=(0, 0, 0, 255))
    config = HeadlineCardConfig(
        corner_fade_color=[0, 200, 0],
        corner_fade_corners=["bottom-left", "top-right"],
        corner_fade_max_alpha=0.8,
        corner_fade_radius_ratio=0.8,
    )
    result = np.asarray(apply_corner_fade(image, config))

    # Нижний-левый и верхний-правый углы должны позеленеть (green-канал вырос).
    assert result[99, 0][1] > 100   # bottom-left
    assert result[0, 99][1] > 100   # top-right
    # Противоположные углы (не в списке) остаются почти чёрными.
    assert result[0, 0][1] < 60     # top-left
    assert result[99, 99][1] < 60   # bottom-right


def test_apply_corner_fade_raises_on_unknown_corner():
    image = Image.new("RGBA", (50, 50))
    config = HeadlineCardConfig(corner_fade_corners=["middle"])
    with pytest.raises(HeadlineCardError):
        apply_corner_fade(image, config)


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
