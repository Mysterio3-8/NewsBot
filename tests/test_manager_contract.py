"""Контракт менеджер ↔ софт: parse/serialize лимитов, рендер manager_contract.yaml."""
from __future__ import annotations

import yaml

from app.manager.contract import CONTRACT_FILENAME, SoftContract, contract_file_path, write_contract_file


def test_empty_contract_roundtrip():
    contract = SoftContract()
    assert contract.is_empty()
    assert contract.to_config_dict() == {}
    assert SoftContract.from_config_json(contract.to_config_json()).is_empty()


def test_config_json_roundtrip_keeps_only_set_fields():
    contract = SoftContract(max_posts_per_day=30, min_interval_minutes=40, max_interval_minutes=75)
    restored = SoftContract.from_config_json(contract.to_config_json())
    assert restored.max_posts_per_day == 30
    assert restored.min_interval_minutes == 40
    assert restored.max_interval_minutes == 75
    assert restored.quiet_start_hour is None
    # None-поля не пишутся в реестр
    assert "quiet_start_hour" not in contract.to_config_dict()["limits"]


def test_from_config_json_tolerates_broken_and_none():
    assert SoftContract.from_config_json(None).is_empty()
    assert SoftContract.from_config_json("{битый").is_empty()
    assert SoftContract.from_config_json("[]").is_empty()


def test_render_yaml_is_valid_yaml_with_limits():
    contract = SoftContract(max_posts_per_day=30, quiet_start_hour=0, quiet_end_hour=7)
    parsed = yaml.safe_load(contract.render_yaml())
    assert parsed["limits"]["max_posts_per_day"] == 30
    assert parsed["limits"]["quiet_start_hour"] == 0


def test_render_summary_lists_limits():
    contract = SoftContract(max_posts_per_day=30, min_interval_minutes=40, max_interval_minutes=75,
                            quiet_start_hour=0, quiet_end_hour=7)
    summary = contract.render_summary()
    assert "30" in summary and "40–75" in summary and "0:00–7:00" in summary


def test_render_summary_empty():
    assert "не заданы" in SoftContract().render_summary()


def test_write_contract_file(tmp_path):
    contract = SoftContract(max_posts_per_day=25, min_interval_minutes=45)
    path = write_contract_file(str(tmp_path), contract)
    assert path == contract_file_path(str(tmp_path))
    assert (tmp_path / CONTRACT_FILENAME).exists()
    parsed = yaml.safe_load((tmp_path / CONTRACT_FILENAME).read_text(encoding="utf-8"))
    assert parsed["limits"]["max_posts_per_day"] == 25
