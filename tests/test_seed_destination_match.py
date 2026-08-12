"""Сид не должен плодить каналы-двойники из-за разной записи одного приёмника.

Подозрение 2026-08-12: у Новостей SEO-теги в постах есть, у Кино нет ни в одном, хотя
обе настройки приходят из одного сида. Один из возможных механизмов — канал-двойник:
`_ensure_channel` искал канал по ТОЧНОМУ совпадению `vk_destination`, а один и тот же
приёмник записывается по-разному («240120678», «club240120678», «-240120678»).
Не узнав существующий канал, сид заводит второй и обновляет настройки ему, а рабочий
остаётся со старыми — снаружи это выглядит как «сид прогнали, а ничего не изменилось».
"""
from __future__ import annotations

import pytest

from app.core.channel_settings import ChannelSettings
from app.db.repository import Repository, init_db, make_engine
from app.seed_channels import normalize_destination, seed_cinema

CINEMA_VK = "240120678"


@pytest.mark.parametrize(
    "written",
    ["240120678", "club240120678", "public240120678", "-240120678", " 240120678 "],
)
def test_same_community_written_differently_is_one_destination(written):
    assert normalize_destination(written) == CINEMA_VK


def test_screen_name_is_left_alone():
    """Короткое имя сообщества — не число, обрезать у него «club» нечего и незачем."""
    assert normalize_destination("kinobestfilmss") == "kinobestfilmss"


def _repo(tmp_path, name: str) -> Repository:
    engine = make_engine(tmp_path / name)
    init_db(engine)
    return Repository(engine)


def test_seed_updates_existing_channel_written_with_club_prefix(tmp_path):
    """Главная регрессия: канал в БД записан как «club240120678», сид ищет «240120678».
    Раньше это давало ВТОРОЙ канал; теперь обновляется существующий."""
    repo = _repo(tmp_path, "cinema.db")
    existing = repo.create_channel(
        name="КиноЛайф - Лучшие фильмы",
        vk_destination=f"club{CINEMA_VK}",
        enabled=True,
    )
    before = len(repo.list_channels())

    seed_cinema(repo)

    assert len(repo.list_channels()) == before, "сид завёл канал-двойник"
    settings = ChannelSettings.from_json(repo.get_channel(existing.id).settings_json)
    assert settings.seo_enabled is True
    assert settings.seo_base_tags, "SEO-теги канала не доехали"


def test_seed_is_idempotent_on_a_normal_destination(tmp_path):
    repo = _repo(tmp_path, "idempotent.db")

    seed_cinema(repo)
    count_after_first = len(repo.list_channels())
    seed_cinema(repo)

    assert len(repo.list_channels()) == count_after_first
