from PIL import Image

from app.core.images.collage_splitter import split_vertical_collage


def test_splits_two_panel_collage(tmp_path):
    """Вытянутое фото с чётким швом между кадрами → 2 отдельных кадра."""
    img = Image.new("RGB", (600, 900))
    img.paste(Image.new("RGB", (600, 450), (200, 30, 30)), (0, 0))    # верхний кадр
    img.paste(Image.new("RGB", (600, 450), (30, 30, 200)), (0, 450))  # нижний кадр
    path = tmp_path / "collage.jpg"
    img.save(path)

    parts = split_vertical_collage(path)

    assert len(parts) == 2
    assert Image.open(parts[0]).size[0] == 600  # ширина сохранена
    assert Image.open(parts[1]).size[0] == 600


def test_does_not_split_landscape_single_photo(tmp_path):
    img = Image.new("RGB", (900, 500), (40, 90, 140))
    path = tmp_path / "wide.jpg"
    img.save(path)

    assert split_vertical_collage(path) == [str(path)]


def test_does_not_split_continuous_portrait(tmp_path):
    """Вытянутое, но БЕЗ шва (плавный градиент) — цельное фото, не режем."""
    img = Image.new("RGB", (600, 900))
    px = img.load()
    for y in range(900):
        for x in range(0, 600, 3):
            v = int(y / 900 * 255)
            px[x, y] = (v, v, v)
            if x + 1 < 600:
                px[x + 1, y] = (v, v, v)
            if x + 2 < 600:
                px[x + 2, y] = (v, v, v)
    path = tmp_path / "gradient.jpg"
    img.save(path)

    assert split_vertical_collage(path) == [str(path)]


def test_missing_file_returns_original_path(tmp_path):
    p = tmp_path / "nope.jpg"
    assert split_vertical_collage(p) == [str(p)]
