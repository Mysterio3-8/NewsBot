"""Управление софтами, развёрнутыми на VPS через systemd.

Разведка 2026-07-21 показала: модель «один софт = один сервис» неверна.
- Минусы (`yt-vk-publisher`) — не демон, а ТАЙМЕР (ежедневный батч) → включать/выключать
  надо `.timer`, а не `.service` (тот `static` и живёт секунды).
- Музыка (`tg-music-bot`) — 9 юнитов (7 сервисов + 2 таймера) → софт = НАБОР юнитов.
Поэтому в реестре хранится список юнитов, а не одна строка.

Бот на VPS работает от root (`User=` пуст в news-rewriter-bot.service), поэтому
`systemctl` доступен без sudo. На Windows-разработке systemctl нет — все функции
деградируют мягко (`systemctl недоступен`), бот не падает.
"""
from __future__ import annotations

import json
import logging
import subprocess

logger = logging.getLogger("app")

SYSTEMCTL_TIMEOUT_SECONDS = 20
UNAVAILABLE = "systemctl недоступен (не Linux/VPS)"


def parse_units(units_json: str | None) -> list[str]:
    """Список юнитов из JSON-поля реестра. Битый JSON — не роняем бот, считаем пустым."""
    if not units_json:
        return []
    try:
        data = json.loads(units_json)
    except json.JSONDecodeError:
        logger.warning("Битый systemd_units_json в реестре софтов: %r", units_json)
        return []
    return [str(unit) for unit in data] if isinstance(data, list) else []


def _run(args: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["systemctl", *args],
            capture_output=True,
            text=True,
            timeout=SYSTEMCTL_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return -1, UNAVAILABLE
    except subprocess.TimeoutExpired:
        return -1, "systemctl не ответил вовремя"
    return proc.returncode, (proc.stdout or proc.stderr).strip()


def unit_state(unit: str) -> str:
    """active / inactive / failed / unknown-состояние одного юнита."""
    code, out = _run(["is-active", unit])
    if code == -1:
        return out
    return out.splitlines()[0].strip() if out else "unknown"


def is_active(units: list[str]) -> bool:
    """True — АКТИВНЫ ВСЕ юниты софта (частично поднятый софт считаем выключенным,
    чтобы кнопка «Включить» доводила его до конца)."""
    if not units:
        return False
    return all(unit_state(unit) == "active" for unit in units)


def start(units: list[str]) -> bool:
    if not units:
        return False
    code, out = _run(["start", *units])
    if code != 0:
        logger.warning("systemctl start %s → %s", units, out)
    return code == 0


def stop(units: list[str]) -> bool:
    if not units:
        return False
    code, out = _run(["stop", *units])
    if code != 0:
        logger.warning("systemctl stop %s → %s", units, out)
    return code == 0


def status_text(units: list[str]) -> str:
    if not units:
        return "Юниты не заданы в реестре."
    lines = [f"{'🟢' if (state := unit_state(unit)) == 'active' else '🔴'} {unit} — {state}"
             for unit in units]
    return "\n".join(lines)
