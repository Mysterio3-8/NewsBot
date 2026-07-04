"""HTTP-клиент FastAPI-сервиса MoneyPrinterTurbo (Shorts) — генерация короткого
видео (TTS + видео-фон + субтитры) из готового текста рерайта новостного поста.

Shorts — отдельный проект и venv (C:\\Users\\Professional\\Desktop\\Shorts), код
не сливаем (см. CLAUDE.md); общаемся только по HTTP, запускается/останавливается
отдельным процессом через ProcessController (см. control_bot.py::/shorts_*).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

TASK_STATE_FAILED = -1
TASK_STATE_COMPLETE = 1
API_PREFIX = "/api/v1"
SHORTS_API_KEY_ENV = "SHORTS_API_KEY"


class ShortsClientError(Exception):
    """Ошибка обращения к Shorts API (сервис недоступен, задача упала, таймаут)."""


def _auth_headers() -> dict:
    """Shorts требует заголовок x-api-key на КАЖДЫЙ запрос, даже когда сам ключ
    не задан в его config.toml (сравнение с пустой строкой) — иначе 401."""
    return {"x-api-key": os.environ.get(SHORTS_API_KEY_ENV, "")}


def create_task(
    base_url: str,
    subject: str,
    script: str,
    *,
    language: str = "ru",
    voice_name: str = "ru-RU-DmitryNeural",
    aspect: str = "9:16",
    timeout: float = 30,
) -> str:
    """Создаёт задачу генерации видео на стороне Shorts, возвращает task_id.

    `script` — уже готовый рерайт-текст поста; Shorts использует его как есть
    (не гоняет свою собственную LLM-генерацию сценария повторно)."""
    response = requests.post(
        f"{base_url}{API_PREFIX}/videos",
        json={
            "video_subject": subject[:500],
            "video_script": script[:8000],
            "video_language": language,
            "voice_name": voice_name,
            "video_aspect": aspect,
            "video_source": "pexels",
            "subtitle_enabled": True,
        },
        headers=_auth_headers(),
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    task_id = data.get("data", {}).get("task_id")
    if not task_id:
        raise ShortsClientError(f"Ответ Shorts без task_id: {data}")
    return task_id


def get_task_status(base_url: str, task_id: str, *, timeout: float = 15) -> dict:
    response = requests.get(
        f"{base_url}{API_PREFIX}/tasks/{task_id}", headers=_auth_headers(), timeout=timeout
    )
    response.raise_for_status()
    return response.json().get("data", {})


def wait_for_video(
    base_url: str,
    task_id: str,
    *,
    poll_interval: float = 5,
    max_wait_seconds: float = 600,
) -> list[str]:
    """Синхронно блокирует до готовности видео (или таймаута/ошибки). Возвращает
    список URL готовых видео (`videos` из ответа Shorts, файлы отдаются статикой)."""
    deadline = time.monotonic() + max_wait_seconds
    while time.monotonic() < deadline:
        status = get_task_status(base_url, task_id)
        state = status.get("state")
        if state == TASK_STATE_FAILED:
            raise ShortsClientError(f"Задача {task_id} завершилась с ошибкой на стороне Shorts")
        if state == TASK_STATE_COMPLETE and status.get("videos"):
            return [_resolve_url(base_url, video) for video in status["videos"]]
        time.sleep(poll_interval)
    raise ShortsClientError(f"Задача {task_id} не завершилась за {max_wait_seconds}с")


def _resolve_url(base_url: str, video_path: str) -> str:
    """Shorts иногда возвращает относительный путь (`/tasks/{id}/final-1.mp4`)
    вместо полного URL — достраиваем его до абсолютного, чтобы download_video
    мог его скачать."""
    if video_path.startswith("http://") or video_path.startswith("https://"):
        return video_path
    return f"{base_url.rstrip('/')}/{video_path.lstrip('/')}"


def download_video(url: str, destination: Path, *, timeout: float = 120) -> None:
    """Скачивает готовое видео по URL (Shorts отдаёт storage/tasks как статику)."""
    response = requests.get(url, timeout=timeout, stream=True)
    response.raise_for_status()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with open(destination, "wb") as file:
        for chunk in response.iter_content(chunk_size=1024 * 256):
            file.write(chunk)
