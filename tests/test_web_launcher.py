"""Веб-лаунчер (Старт/Стоп поверх ServiceController). Управление сервисом подменяется
стабом контроллера — реальный поток/сервис в юнит-тестах Flask-слоя не нужен
(логика ServiceController покрыта в test_service_controller.py)."""
from __future__ import annotations

import datetime

import pytest

import app.web_launcher as web_launcher


class FakeController:
    def __init__(self):
        self._running = False
        self.started_at = None

    def is_running(self):
        return self._running

    def start(self):
        if self._running:
            return False
        self._running = True
        self.started_at = datetime.datetime(2026, 7, 4, 9, 40, tzinfo=datetime.timezone.utc)
        return True

    def stop(self):
        if not self._running:
            return False
        self._running = False
        return True

    def recent_published(self, limit=10):
        return []


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(web_launcher, "_controller", FakeController())
    web_launcher.app.testing = True
    return web_launcher.app.test_client()


def test_format_moscow_time_converts_naive_utc():
    dt = datetime.datetime(2026, 7, 3, 9, 40)  # naive, хранится как UTC
    assert web_launcher.format_moscow_time(dt) == "03.07.2026 12:40"


def test_format_moscow_time_none_returns_none():
    assert web_launcher.format_moscow_time(None) is None


def test_status_reports_stopped_before_start(client):
    assert client.get("/status").get_json()["running"] is False


def test_start_then_status_reports_running(client):
    assert client.post("/start").get_json()["ok"] is True
    status = client.get("/status").get_json()
    assert status["running"] is True
    assert status["started_at"] is not None


def test_start_twice_returns_already_running(client):
    client.post("/start")
    assert client.post("/start").get_json()["ok"] is False


def test_stop_without_start_returns_already_stopped(client):
    assert client.post("/stop").get_json()["ok"] is False


def test_start_then_stop_reports_stopped(client):
    client.post("/start")
    assert client.post("/stop").get_json()["ok"] is True
    assert client.get("/status").get_json()["running"] is False


def test_index_page_loads(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"AI News Rewriter" in response.data
