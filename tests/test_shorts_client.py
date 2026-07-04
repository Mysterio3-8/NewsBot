"""Shorts-клиент — HTTP-вызовы к MoneyPrinterTurbo мокаются на уровне requests.*,
т.к. реальный сервис (Shorts, отдельный venv) не поднят в тестах этого проекта."""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.core.shorts import client


def make_response(json_data, status=200):
    response = Mock()
    response.json.return_value = json_data
    response.raise_for_status = Mock()
    return response


def test_create_task_returns_task_id(monkeypatch):
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return make_response({"status": 200, "message": "success", "data": {"task_id": "abc-123"}})

    monkeypatch.setattr(client.requests, "post", fake_post)
    task_id = client.create_task("http://127.0.0.1:8080", "Тема", "Текст сценария")

    assert task_id == "abc-123"
    assert captured["url"] == "http://127.0.0.1:8080/api/v1/videos"
    assert captured["json"]["video_subject"] == "Тема"
    assert captured["json"]["video_script"] == "Текст сценария"
    assert captured["json"]["video_language"] == "ru"
    assert "x-api-key" in captured["headers"]


def test_create_task_raises_without_task_id(monkeypatch):
    monkeypatch.setattr(
        client.requests, "post",
        lambda *a, **k: make_response({"status": 200, "message": "success", "data": {}}),
    )
    with pytest.raises(client.ShortsClientError):
        client.create_task("http://127.0.0.1:8080", "Тема", "Текст")


def test_get_task_status_returns_data(monkeypatch):
    monkeypatch.setattr(
        client.requests, "get",
        lambda url, headers, timeout: make_response({"status": 200, "message": "success", "data": {"state": 4, "progress": 50}}),
    )
    status = client.get_task_status("http://127.0.0.1:8080", "abc-123")
    assert status == {"state": 4, "progress": 50}


def test_wait_for_video_returns_urls_when_complete(monkeypatch):
    calls = {"count": 0}

    def fake_get_status(base_url, task_id, timeout=15):
        calls["count"] += 1
        if calls["count"] < 2:
            return {"state": 4, "progress": 50}
        return {"state": client.TASK_STATE_COMPLETE, "progress": 100, "videos": ["http://127.0.0.1:8080/tasks/abc-123/final-1.mp4"]}

    monkeypatch.setattr(client, "get_task_status", fake_get_status)
    videos = client.wait_for_video("http://127.0.0.1:8080", "abc-123", poll_interval=0)

    assert videos == ["http://127.0.0.1:8080/tasks/abc-123/final-1.mp4"]
    assert calls["count"] == 2


def test_wait_for_video_resolves_relative_paths_to_absolute_urls(monkeypatch):
    monkeypatch.setattr(
        client, "get_task_status",
        lambda *a, **k: {"state": client.TASK_STATE_COMPLETE, "progress": 100, "videos": ["/tasks/abc-123/final-1.mp4"]},
    )
    videos = client.wait_for_video("http://127.0.0.1:8080", "abc-123", poll_interval=0)
    assert videos == ["http://127.0.0.1:8080/tasks/abc-123/final-1.mp4"]


def test_wait_for_video_raises_on_failed_state(monkeypatch):
    monkeypatch.setattr(client, "get_task_status", lambda *a, **k: {"state": client.TASK_STATE_FAILED})
    with pytest.raises(client.ShortsClientError):
        client.wait_for_video("http://127.0.0.1:8080", "abc-123", poll_interval=0)


def test_wait_for_video_raises_on_timeout(monkeypatch):
    monkeypatch.setattr(client, "get_task_status", lambda *a, **k: {"state": 4, "progress": 10})
    with pytest.raises(client.ShortsClientError):
        client.wait_for_video("http://127.0.0.1:8080", "abc-123", poll_interval=0, max_wait_seconds=0)


def test_download_video_writes_chunks(monkeypatch, tmp_path):
    response = Mock()
    response.raise_for_status = Mock()
    response.iter_content = Mock(return_value=[b"chunk1", b"chunk2"])
    monkeypatch.setattr(client.requests, "get", lambda url, timeout, stream: response)

    destination = tmp_path / "out" / "video.mp4"
    client.download_video("http://127.0.0.1:8080/tasks/abc/final-1.mp4", destination)

    assert destination.read_bytes() == b"chunk1chunk2"
