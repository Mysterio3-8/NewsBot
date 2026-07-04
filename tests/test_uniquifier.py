import numpy as np
from PIL import Image

from app.config.loader import UniquifyConfig
from app.core.images.uniquifier import uniquify


def _solid_image(color=(120, 130, 140), size=(200, 200)) -> Image.Image:
    return Image.new("RGB", size, color)


def test_uniquify_disabled_returns_same_pixels():
    image = _solid_image()
    result = uniquify(image, UniquifyConfig(enabled=False))
    assert np.array_equal(np.asarray(image), np.asarray(result))


def test_uniquify_keeps_original_dimensions():
    image = _solid_image(size=(640, 480))
    result = uniquify(image, UniquifyConfig(enabled=True, crop_percent=1.0, noise_sigma=3.0))
    assert result.size == (640, 480)


def test_uniquify_changes_pixels_when_enabled():
    image = _solid_image()
    result = uniquify(image, UniquifyConfig(enabled=True, crop_percent=1.0, noise_sigma=3.0))
    assert not np.array_equal(np.asarray(image), np.asarray(result.convert("RGB")))


def test_uniquify_noise_is_bounded_and_subtle():
    """Шум должен быть слабым — средняя разница на пиксель заметно меньше 255,
    иначе картинка визуально портится."""
    image = _solid_image(color=(128, 128, 128), size=(300, 300))
    result = uniquify(image, UniquifyConfig(enabled=True, crop_percent=0.0, noise_sigma=3.0))
    diff = np.abs(np.asarray(image, dtype=np.int16) - np.asarray(result.convert("RGB"), dtype=np.int16))
    assert diff.mean() < 10  # слабый шум, а не мусор


def test_uniquify_preserves_alpha_channel():
    image = Image.new("RGBA", (100, 100), (100, 100, 100, 200))
    result = uniquify(image, UniquifyConfig(enabled=True, crop_percent=1.0, noise_sigma=2.0))
    assert result.mode == "RGBA"
    alpha = np.asarray(result.split()[3])
    assert np.all(alpha == 200)


def test_uniquify_only_crop_no_noise_still_changes():
    image = Image.effect_noise((200, 200), 50).convert("RGB")  # текстура, чтобы кроп был виден
    result = uniquify(image, UniquifyConfig(enabled=True, crop_percent=2.0, noise_sigma=0.0))
    assert not np.array_equal(np.asarray(image), np.asarray(result))
