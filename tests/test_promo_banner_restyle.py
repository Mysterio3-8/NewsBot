"""Перенос и перекраска чужой промо-плашки (ТЗ 2026-08-10)."""
import numpy as np
from PIL import Image

from app.core.images.promo_banner import has_promo_banner, restyle_promo_banner

YELLOW = (240, 220, 40)
SCENE = (40, 90, 140)
BAR_HEIGHT = 60
WIDTH, HEIGHT = 600, 400


def _frame_with_bar(tmp_path, name: str, *, at_top: bool, bar_width: int = WIDTH) -> Image.Image:
    """Кадр с жёлтой плашкой и тёмным текстом на ней."""
    image = Image.new("RGB", (WIDTH, HEIGHT), SCENE)
    bar = Image.new("RGB", (bar_width, BAR_HEIGHT), YELLOW)
    # «Буквы»: тёмные полосы внутри плашки — их перекраска обязана сохранить.
    for offset in range(10, bar_width - 10, 40):
        bar.paste(Image.new("RGB", (14, 24), (20, 20, 20)), (offset, 18))
    image.paste(bar, (0, 0 if at_top else HEIGHT - BAR_HEIGHT))
    path = tmp_path / name
    image.save(path)
    return path


def _row_is_yellow(pixels: np.ndarray, y: int) -> bool:
    row = pixels[y]
    return bool(((row[:, 0] > 150) & (row[:, 1] > 150) & (row[:, 2] < 120)).mean() > 0.5)


def test_detects_banner(tmp_path):
    assert has_promo_banner(_frame_with_bar(tmp_path, "top.png", at_top=True))


def test_top_banner_moves_to_bottom_and_changes_color(tmp_path):
    path = _frame_with_bar(tmp_path, "top.png", at_top=True)

    restyled = restyle_promo_banner(path)

    assert restyled is not None
    pixels = np.asarray(Image.open(restyled).convert("RGB")).astype(np.int16)
    assert not _row_is_yellow(pixels, 5)                 # сверху жёлтого больше нет
    assert not _row_is_yellow(pixels, HEIGHT - 5)        # и внизу плашка уже не жёлтая
    assert not has_promo_banner(restyled)                # детектор её больше не видит


def test_restyled_frame_keeps_size(tmp_path):
    path = _frame_with_bar(tmp_path, "top.png", at_top=True)
    restyled = restyle_promo_banner(path)
    assert Image.open(restyled).size == (WIDTH, HEIGHT)


def test_letters_survive_recolor(tmp_path):
    """Внутри перекрашенной полосы должны остаться ДВА цвета — фон и буквы."""
    path = _frame_with_bar(tmp_path, "top.png", at_top=True)
    restyled = restyle_promo_banner(path)

    bar = np.asarray(Image.open(restyled).convert("RGB"))[HEIGHT - BAR_HEIGHT :]
    assert len(np.unique(bar.reshape(-1, 3), axis=0)) > 1


def test_bottom_banner_moves_to_top(tmp_path):
    path = _frame_with_bar(tmp_path, "bottom.png", at_top=False)

    restyled = restyle_promo_banner(path)

    pixels = np.asarray(Image.open(restyled).convert("RGB")).astype(np.int16)
    assert not _row_is_yellow(pixels, HEIGHT - 5)
    # Сюжет уехал вниз: строка сразу под перекрашенной полосой — это кадр, не плашка.
    assert tuple(pixels[BAR_HEIGHT + 5, WIDTH // 2]) == SCENE


def test_recolor_is_deterministic_for_same_file_name(tmp_path):
    """Один и тот же кадр всегда перекрашивается одинаково — цвет берётся из хеша имени."""
    other_dir = tmp_path / "sub"
    other_dir.mkdir()
    first = restyle_promo_banner(_frame_with_bar(tmp_path, "a.png", at_top=True))
    second = restyle_promo_banner(_frame_with_bar(other_dir, "a.png", at_top=True))

    assert np.array_equal(
        np.asarray(Image.open(first).convert("RGB"))[-BAR_HEIGHT:],
        np.asarray(Image.open(second).convert("RGB"))[-BAR_HEIGHT:],
    )


def test_narrow_sticker_is_not_restyled(tmp_path):
    """Плашка-наклейка, а не полоса во всю ширину: перенести нельзя — кадр выбрасываем."""
    assert restyle_promo_banner(
        _frame_with_bar(tmp_path, "narrow.png", at_top=True, bar_width=150)
    ) is None


def test_banner_in_the_middle_is_not_restyled(tmp_path):
    image = Image.new("RGB", (WIDTH, HEIGHT), SCENE)
    image.paste(Image.new("RGB", (WIDTH, BAR_HEIGHT), YELLOW), (0, HEIGHT // 2))
    path = tmp_path / "middle.png"
    image.save(path)

    assert restyle_promo_banner(path) is None


def test_frame_without_banner_returns_none(tmp_path):
    path = tmp_path / "clean.png"
    Image.new("RGB", (WIDTH, HEIGHT), SCENE).save(path)

    assert restyle_promo_banner(path) is None
