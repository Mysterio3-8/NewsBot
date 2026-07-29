"""Пульт альбомного потока Музыки (SoundCloud → VK) внутри бота-менеджера.

Бот не знает ни про yt-dlp, ни про ffmpeg, ни про VK: он вызывает CLI софта
(`app/soundcloud_cli.py`) по пути из реестра и показывает его JSON-ответ. Так
проекты остаются независимыми — общий у них только формат вызова, не код.

Софт объявляет поддержку флагом в реестре: config_json = {"soundcloud": true}.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("app")

ENQUEUE_TIMEOUT_SECONDS = 180  # читает плейлист по сети — бывает небыстро
STATUS_TIMEOUT_SECONDS = 30

CLI_RELATIVE_PATH = "app/soundcloud_cli.py"


def supports_soundcloud(record) -> bool:
    """Есть ли у софта альбомный поток. Битый config_json — не роняем бот."""
    if record is None or not record.project_path:
        return False
    try:
        data = json.loads(record.config_json or "{}")
    except json.JSONDecodeError:
        logger.warning("Битый config_json у софта %s", record.soft_id)
        return False
    return bool(isinstance(data, dict) and data.get("soundcloud"))


def _python_executable(project_path: Path) -> str:
    """Интерпретатор venv софта. У каждого проекта свой — общий не подойдёт."""
    for candidate in (project_path / "venv/bin/python", project_path / "venv/Scripts/python.exe"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _run_cli(project_path: str, args: list[str], timeout: int) -> dict:
    path = Path(project_path)
    if not (path / CLI_RELATIVE_PATH).exists():
        return {"ok": False, "error": f"CLI софта не найден: {path / CLI_RELATIVE_PATH}"}

    command = [_python_executable(path), CLI_RELATIVE_PATH, *args]
    try:
        proc = subprocess.run(
            command, cwd=str(path), capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        return {"ok": False, "error": "Не найден интерпретатор Python софта"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Софт не ответил вовремя"}

    output = (proc.stdout or proc.stderr or "").strip()
    try:
        return json.loads(output.splitlines()[-1]) if output else {"ok": False, "error": "Пустой ответ"}
    except (json.JSONDecodeError, IndexError):
        logger.warning("Не разобрать ответ CLI %s: %r", args, output)
        return {"ok": False, "error": output or "Софт вернул неразборчивый ответ"}


async def enqueue(project_path: str, url: str, chat_id: int) -> dict:
    return await asyncio.to_thread(
        _run_cli,
        project_path,
        ["enqueue", url, "--chat-id", str(chat_id)],
        ENQUEUE_TIMEOUT_SECONDS,
    )


async def status(project_path: str) -> dict:
    return await asyncio.to_thread(_run_cli, project_path, ["status"], STATUS_TIMEOUT_SECONDS)


def render_enqueue_result(payload: dict) -> str:
    if not payload.get("ok"):
        return f"❌ Не получилось: {payload.get('error', 'неизвестная ошибка')}"

    lines = [
        f"✅ Принял: «{payload.get('title') or 'без названия'}»",
        f"Треков: {payload.get('tracks', 0)}",
    ]
    ahead = payload.get("ahead_in_queue", 0)
    if ahead:
        lines.append(f"Перед ним в очереди: {ahead} — начнётся после них.")
    else:
        lines.append("Начну на ближайшем прогоне. Как всё опубликую — напишу.")
    return "\n".join(lines)


def render_status(payload: dict) -> str:
    if not payload.get("ok"):
        return f"❌ Не получилось: {payload.get('error', 'неизвестная ошибка')}"

    active = payload.get("active")
    pending = payload.get("pending_albums", 0)
    if not active:
        return f"Сейчас ничего не публикуется.\nВ очереди альбомов: {pending}"

    lines = [
        f"▶️ «{active.get('title') or 'без названия'}» — {active.get('status')}",
        f"Треков осталось: {active.get('tracks_left')} из {active.get('tracks_total')}",
    ]
    if active.get("next_post_at"):
        lines.append(f"Следующий трек: {_format_moment(active['next_post_at'])}")
    if pending:
        lines.append(f"Ждут своей очереди: {pending}")
    return "\n".join(lines)


def _format_moment(raw: str) -> str:
    from datetime import datetime

    try:
        return datetime.fromisoformat(raw).strftime("%d.%m %H:%M")
    except ValueError:
        return raw
