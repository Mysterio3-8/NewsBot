from PIL import Image

from app.core.images.promo_banner import has_promo_banner


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
