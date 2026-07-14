from PIL import Image

from app.core.images.promo_banner import crop_to_clean_frame, has_promo_banner


def test_detects_dense_yellow_banner(tmp_path):
    """Плотный ярко-жёлтый прямоугольник (промо-плашка) — детектится."""
    img = Image.new("RGB", (800, 600), color=(30, 40, 60))  # тёмный кадр
    banner = Image.new("RGB", (300, 120), color=(240, 220, 20))  # жёлтая плашка
    img.paste(banner, (450, 400))
    path = tmp_path / "banner.jpg"
    img.save(path)

    assert has_promo_banner(path) is True


def test_ignores_photo_without_yellow(tmp_path):
    img = Image.new("RGB", (800, 600), color=(40, 90, 140))
    path = tmp_path / "clean.jpg"
    img.save(path)

    assert has_promo_banner(path) is False


def test_ignores_small_scattered_yellow(tmp_path):
    """Мелкий жёлтый объект в сцене (не плашка) — не срабатывает."""
    img = Image.new("RGB", (800, 600), color=(40, 90, 140))
    dot = Image.new("RGB", (12, 12), color=(240, 220, 20))
    img.paste(dot, (100, 100))  # крошечное жёлтое пятно
    path = tmp_path / "dot.jpg"
    img.save(path)

    assert has_promo_banner(path) is False


def test_missing_file_returns_false(tmp_path):
    assert has_promo_banner(tmp_path / "does_not_exist.jpg") is False


def _collage_with_banner(tmp_path, banner_y):
    """Вертикальный 2-кадровый коллаж с жёлтой плашкой на заданной высоте."""
    img = Image.new("RGB", (800, 1200), color=(30, 40, 60))
    banner = Image.new("RGB", (300, 120), color=(240, 220, 20))
    img.paste(banner, (450, banner_y))
    path = tmp_path / f"collage_{banner_y}.jpg"
    img.save(path)
    return path


def test_crop_to_clean_frame_keeps_top_when_banner_in_bottom(tmp_path):
    """Плашка в нижнем кадре → обрезаем до верхнего чистого кадра."""
    path = _collage_with_banner(tmp_path, banner_y=850)  # ниже середины (600)
    cropped = crop_to_clean_frame(path)

    assert cropped is not None
    assert has_promo_banner(cropped) is False
    assert Image.open(cropped).height == 600  # верхняя половина


def test_crop_to_clean_frame_keeps_bottom_when_banner_in_top(tmp_path):
    path = _collage_with_banner(tmp_path, banner_y=200)  # выше середины
    cropped = crop_to_clean_frame(path)

    assert cropped is not None
    assert has_promo_banner(cropped) is False


def test_crop_to_clean_frame_returns_none_when_banner_straddles_middle(tmp_path):
    """Плашка через середину — одним кадром не изолировать → None (фото не брать)."""
    path = _collage_with_banner(tmp_path, banner_y=540)  # 540..660, через середину 600
    assert crop_to_clean_frame(path) is None


def test_crop_to_clean_frame_returns_none_without_banner(tmp_path):
    img = Image.new("RGB", (800, 1200), color=(40, 90, 140))
    path = tmp_path / "clean.jpg"
    img.save(path)
    assert crop_to_clean_frame(path) is None
