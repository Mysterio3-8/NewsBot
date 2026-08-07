"""Управление лимитами ВНЕШНИХ софтов из бота (через контракт).

Раньше кнопки Лимит/Интервал у Альбомов и Музыки были заглушкой soft:na — теперь
пишут в контракт реестра и рендерят manager_contract.yaml в каталог софта.
"""
from __future__ import annotations

import json

import app.control_bot as bot
from app.manager.contract import CONTRACT_FILENAME, SoftContract
from app.manager.repository import (
    ManagerRepository,
    init_manager_db,
    make_manager_engine,
)


def make_repo(tmp_path) -> ManagerRepository:
    engine = make_manager_engine(tmp_path / "manager.db")
    init_manager_db(engine)
    return ManagerRepository(engine)


def _contract_of(repo, soft_id="p_music") -> SoftContract:
    return SoftContract.from_config_json(repo.get_soft(soft_id).config_json)


def test_set_max_posts(tmp_path):
    repo = make_repo(tmp_path)
    repo.upsert_soft("p_music", title="Музыка")
    assert "✅" in bot.save_soft_limit(repo, "p_music", "maxposts", "24")
    assert _contract_of(repo).max_posts_per_day == 24


def test_max_posts_rejects_non_number(tmp_path):
    repo = make_repo(tmp_path)
    repo.upsert_soft("p_music", title="Музыка")
    assert "целое число" in bot.save_soft_limit(repo, "p_music", "maxposts", "много")
    assert _contract_of(repo).max_posts_per_day is None


def test_interval_accepts_range(tmp_path):
    repo = make_repo(tmp_path)
    repo.upsert_soft("p_music", title="Музыка")
    bot.save_soft_limit(repo, "p_music", "interval", "55-65")
    contract = _contract_of(repo)
    assert contract.min_interval_minutes == 55
    assert contract.max_interval_minutes == 65


def test_interval_accepts_single_number(tmp_path):
    repo = make_repo(tmp_path)
    repo.upsert_soft("p_music", title="Музыка")
    bot.save_soft_limit(repo, "p_music", "interval", "60")
    contract = _contract_of(repo)
    assert contract.min_interval_minutes == 60
    assert contract.max_interval_minutes is None


def test_interval_rejects_reversed_range(tmp_path):
    repo = make_repo(tmp_path)
    repo.upsert_soft("p_music", title="Музыка")
    assert "меньше левой" in bot.save_soft_limit(repo, "p_music", "interval", "65-55")


def test_quiet_hours_set_and_off(tmp_path):
    repo = make_repo(tmp_path)
    repo.upsert_soft("p_music", title="Музыка")
    bot.save_soft_limit(repo, "p_music", "quiet", "0-7")
    assert _contract_of(repo).quiet_start_hour == 0
    assert _contract_of(repo).quiet_end_hour == 7
    bot.save_soft_limit(repo, "p_music", "quiet", "выкл")
    assert _contract_of(repo).quiet_start_hour is None


def test_quiet_rejects_bad_hours(tmp_path):
    repo = make_repo(tmp_path)
    repo.upsert_soft("p_music", title="Музыка")
    assert "от 0 до 23" in bot.save_soft_limit(repo, "p_music", "quiet", "0-30")


def test_unknown_soft(tmp_path):
    repo = make_repo(tmp_path)
    assert "не найден" in bot.save_soft_limit(repo, "p_ghost", "maxposts", "5")


def test_writes_contract_file_when_path_known(tmp_path):
    repo = make_repo(tmp_path)
    project = tmp_path / "soft"
    project.mkdir()
    repo.upsert_soft("p_music", title="Музыка", project_path=str(project))
    result = bot.save_soft_limit(repo, "p_music", "maxposts", "24")
    assert "Контракт записан" in result
    assert (project / CONTRACT_FILENAME).exists()


def test_keeps_other_config_keys(tmp_path):
    """Флаг возможностей софта (напр. soundcloud) не должен затираться лимитами."""
    repo = make_repo(tmp_path)
    repo.upsert_soft("p_minus", title="Альбомы",
                     config_json=json.dumps({"soundcloud": True}))
    bot.save_soft_limit(repo, "p_minus", "maxposts", "24")
    config = json.loads(repo.get_soft("p_minus").config_json)
    assert config["soundcloud"] is True
    assert config["limits"]["max_posts_per_day"] == 24


def test_render_contract_shows_limits(tmp_path):
    repo = make_repo(tmp_path)
    repo.upsert_soft("p_music", title="🎵 Музыка", project_path="/opt/x")
    bot.save_soft_limit(repo, "p_music", "maxposts", "24")
    text = bot.render_soft_contract(repo, "p_music")
    assert "🎵 Музыка" in text and "24" in text and "/opt/x" in text


def test_render_contract_unknown_soft(tmp_path):
    assert "не найден" in bot.render_soft_contract(make_repo(tmp_path), "p_ghost")
