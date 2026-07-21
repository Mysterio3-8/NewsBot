"""Управление софтами через systemd: разбор юнитов + вызовы systemctl (замоканные)."""
from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

from app.manager import systemd


def _completed(returncode: int, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_parse_units_reads_json_list():
    assert systemd.parse_units(json.dumps(["a.service", "b.timer"])) == ["a.service", "b.timer"]


def test_parse_units_tolerates_empty_and_broken():
    assert systemd.parse_units(None) == []
    assert systemd.parse_units("") == []
    assert systemd.parse_units("{не json") == []
    assert systemd.parse_units(json.dumps({"a": 1})) == []  # не список


def test_is_active_true_only_when_all_units_active():
    with patch("subprocess.run", return_value=_completed(0, "active\n")):
        assert systemd.is_active(["a.service", "b.timer"]) is True


def test_is_active_false_if_any_unit_down():
    outputs = [_completed(0, "active\n"), _completed(3, "inactive\n")]
    with patch("subprocess.run", side_effect=outputs):
        assert systemd.is_active(["a.service", "b.service"]) is False


def test_is_active_false_without_units():
    assert systemd.is_active([]) is False


def test_start_and_stop_pass_all_units_in_one_call():
    with patch("subprocess.run", return_value=_completed(0)) as run:
        assert systemd.start(["a.service", "b.timer"]) is True
        assert run.call_args[0][0] == ["systemctl", "start", "a.service", "b.timer"]
    with patch("subprocess.run", return_value=_completed(0)) as run:
        assert systemd.stop(["a.service"]) is True
        assert run.call_args[0][0] == ["systemctl", "stop", "a.service"]


def test_start_reports_failure():
    with patch("subprocess.run", return_value=_completed(1, "Failed")):
        assert systemd.start(["a.service"]) is False


def test_degrades_gracefully_without_systemctl():
    """На Windows-разработке systemctl нет — бот не должен падать."""
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert systemd.unit_state("a.service") == systemd.UNAVAILABLE
        assert systemd.is_active(["a.service"]) is False
        assert systemd.start(["a.service"]) is False


def test_status_text_lists_each_unit():
    with patch("subprocess.run", return_value=_completed(0, "active\n")):
        text = systemd.status_text(["a.service", "b.timer"])
    assert "a.service — active" in text and "b.timer — active" in text
