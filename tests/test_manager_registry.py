"""БД-реестр менеджера софтов: CRUD + идемпотентный сид + миграция."""
from __future__ import annotations

import json

from app.manager.repository import (
    ManagerRepository,
    init_manager_db,
    make_manager_engine,
    seed_default_softs,
)


def make_repo(tmp_path) -> ManagerRepository:
    engine = make_manager_engine(tmp_path / "manager.db")
    init_manager_db(engine)
    return ManagerRepository(engine)


def test_upsert_creates_then_updates(tmp_path):
    repo = make_repo(tmp_path)
    repo.upsert_soft("p_nature", title="Природа", path_env="NATURE_BOT_PATH")
    assert repo.get_soft("p_nature").title == "Природа"
    repo.upsert_soft("p_nature", title="Природа VK")
    assert repo.get_soft("p_nature").title == "Природа VK"
    assert len(repo.list_softs()) == 1  # не задублировалось


def test_seed_is_idempotent_and_keeps_enabled(tmp_path):
    repo = make_repo(tmp_path)
    seed_default_softs(repo)
    ids = {s.soft_id for s in repo.list_softs()}
    assert {"p_nature", "p_shorts", "p_minus", "p_music"} <= ids
    # владелец выключил софт из бота — повторный сид не должен его снова включать
    repo.set_enabled("p_minus", False)
    seed_default_softs(repo)
    assert repo.get_soft("p_minus").enabled is False


def test_list_softs_sorted_by_order(tmp_path):
    repo = make_repo(tmp_path)
    seed_default_softs(repo)
    orders = [s.sort_order for s in repo.list_softs()]
    assert orders == sorted(orders)


def test_update_config_roundtrip(tmp_path):
    repo = make_repo(tmp_path)
    repo.upsert_soft("p_music", title="Музыка")
    repo.update_config("p_music", {"max_per_day": 5, "interval_min": 120})
    assert json.loads(repo.get_soft("p_music").config_json) == {"max_per_day": 5, "interval_min": 120}


def test_delete_soft(tmp_path):
    repo = make_repo(tmp_path)
    repo.upsert_soft("p_x", title="X")
    repo.delete_soft("p_x")
    assert repo.get_soft("p_x") is None


def test_default_softs_carry_real_vps_units(tmp_path):
    """Юниты выяснены разведкой VPS: Минусы — таймер (батч), Музыка — набор из 9 юнитов.
    Природа/Shorts на VPS не развёрнуты → host=local, юнитов нет."""
    repo = make_repo(tmp_path)
    seed_default_softs(repo)
    softs = {s.soft_id: s for s in repo.list_softs()}
    assert all(s.kind == "process" for s in softs.values())

    assert softs["p_minus"].host == "vps"
    assert json.loads(softs["p_minus"].systemd_units_json) == ["yt-vk-publisher.timer"]

    music_units = json.loads(softs["p_music"].systemd_units_json)
    assert softs["p_music"].host == "vps"
    assert "tg-music-bot.service" in music_units and len(music_units) == 9

    assert softs["p_nature"].host == "local"
    assert softs["p_nature"].systemd_units_json is None
