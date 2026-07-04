"""ProcessController — реальные короткоживущие subprocess'ы (не моки), чтобы ловить
настоящие баги старта/остановки/логирования, а не поведение мока."""
from __future__ import annotations

import sys
import time

from app.process_controller import ProcessController


def make_controller(tmp_path, code: str) -> ProcessController:
    return ProcessController(
        name="test",
        command=[sys.executable, "-c", code],
        cwd=tmp_path,
        log_path=tmp_path / "logs" / "test.log",
    )


def test_start_reports_running_and_second_start_is_noop(tmp_path):
    controller = make_controller(tmp_path, "import time; time.sleep(2)")
    assert controller.start() is True
    assert controller.is_running() is True
    assert controller.start() is False  # уже запущен
    controller.stop()


def test_stop_terminates_process(tmp_path):
    controller = make_controller(tmp_path, "import time; time.sleep(30)")
    controller.start()
    assert controller.is_running() is True
    assert controller.stop() is True
    assert controller.is_running() is False


def test_stop_without_start_is_noop(tmp_path):
    controller = make_controller(tmp_path, "pass")
    assert controller.stop() is False


def test_is_running_false_after_process_exits_on_its_own(tmp_path):
    controller = make_controller(tmp_path, "pass")
    controller.start()
    for _ in range(50):
        if not controller.is_running():
            break
        time.sleep(0.1)
    assert controller.is_running() is False


def test_tail_log_captures_stdout(tmp_path):
    controller = make_controller(tmp_path, "print('hello from subprocess')")
    controller.start()
    for _ in range(50):
        if not controller.is_running():
            break
        time.sleep(0.1)
    assert "hello from subprocess" in controller.tail_log()


def test_tail_log_before_start_reports_no_log(tmp_path):
    controller = make_controller(tmp_path, "pass")
    assert controller.tail_log() == "(лога пока нет)"


def test_started_at_set_on_start(tmp_path):
    controller = make_controller(tmp_path, "import time; time.sleep(1)")
    assert controller.started_at is None
    controller.start()
    assert controller.started_at is not None
    controller.stop()
