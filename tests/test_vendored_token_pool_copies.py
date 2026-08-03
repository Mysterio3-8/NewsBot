"""Вендоренный `vk_token_pool.py` обязан быть побайтово одинаковым во всех софтах.

Файл намеренно скопирован в три репозитория (SPEC_TOKEN_BALANCER.md, раздел 3.2), чтобы
поломка одного софта не роняла остальные. Цена решения — риск разъехавшихся копий:
общий файл счётчиков читают все три, и разная логика выбора сломала бы равномерность
молча. Этот тест ловит рассинхрон сразу.

Соседние репозитории есть только на машине разработчика — на CI/проде тест пропускается.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "app/core/publishing/vk_token_pool.py"
ALL_AUTO = Path(__file__).resolve().parents[2]
COPIES = (
    ALL_AUTO / "MinusSoft/vk_token_pool.py",
    ALL_AUTO / "TelegramMusicSoft/app/vk_token_pool.py",
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("copy", COPIES, ids=lambda p: p.parent.name)
def test_vendored_copy_matches_source(copy: Path):
    if not copy.exists():
        pytest.skip(f"соседний софт недоступен: {copy}")
    assert _digest(copy) == _digest(SOURCE), (
        f"{copy} разошёлся с эталоном {SOURCE}. Скопируй эталон поверх копии — "
        "иначе софты считают общие счётчики по разным правилам."
    )
