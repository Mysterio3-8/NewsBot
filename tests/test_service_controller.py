"""ServiceController — старт/стоп фонового сервиса. run_forever подменяется быстрым
стабом, ждущим stop_event (реальный APScheduler/LLM не нужен, покрыты отдельно)."""
from __future__ import annotations

import pytest

import app.service_controller as sc_module
from app.db.repository import make_engine as real_make_engine
from app.service_controller import ServiceController


async def _fake_run_forever(repo, config, llm_client, *, stop_event=None):
    if stop_event is not None:
        await stop_event.wait()


class _FakeLoggingConfig:
    level = "INFO"
    max_file_size_mb = 10
    backup_count = 5


class _FakeLLMConfig:
    provider = "groq"
    host = ""
    model = "x"
    api_key_env = "GROQ_API_KEY"
    temperature = 0.7
    top_p = 0.9
    timeout_seconds = 5
    retries = 0
    fallback_models: list[str] = []


class _FakeConfig:
    logging = _FakeLoggingConfig()
    llm = _FakeLLMConfig()


@pytest.fixture
def controller(tmp_path, monkeypatch):
    monkeypatch.setattr(sc_module, "run_forever", _fake_run_forever)
    monkeypatch.setattr(sc_module, "make_engine", lambda: real_make_engine(tmp_path / "test.db"))
    monkeypatch.setattr(sc_module, "load_dotenv", lambda: None)
    monkeypatch.setattr(sc_module, "setup_logging", lambda config: None)
    monkeypatch.setattr(sc_module, "LLMClient", lambda cfg: object())

    ctrl = ServiceController()
    monkeypatch.setattr(ctrl, "_load_config", lambda: _FakeConfig())
    yield ctrl

    if ctrl.is_running():
        ctrl.stop()


def test_starts_and_reports_running(controller):
    assert controller.is_running() is False
    assert controller.start() is True
    assert controller.is_running() is True
    assert controller.started_at is not None


def test_start_twice_returns_false(controller):
    controller.start()
    assert controller.start() is False


def test_stop_returns_true_then_false(controller):
    controller.start()
    assert controller.stop() is True
    assert controller.is_running() is False
    assert controller.stop() is False


def test_recent_published_empty_before_start(controller):
    assert controller.recent_published() == []
