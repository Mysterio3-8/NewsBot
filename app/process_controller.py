"""Запуск/остановка ВНЕШНЕГО процесса (свой репозиторий, свой venv) из этого приложения.

Отличие от `service_controller.py`: тот управляет asyncio-задачей в ЭТОМ ЖЕ процессе
и venv. `ProcessController` нужен, когда управляемый бот — отдельный проект с
собственными зависимостями (напр. vk-nature-bot), который мы не сливаем в один
кодбейз (см. CLAUDE.md), но хотим стартовать/останавливать/смотреть статус из
одного Telegram-пульта."""
from __future__ import annotations

import datetime
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("app")


class ProcessController:
    def __init__(self, name: str, command: list[str], cwd: Path, log_path: Path) -> None:
        self.name = name
        self._command = command
        self._cwd = cwd
        self._log_path = log_path
        self._process: subprocess.Popen | None = None
        self._started_at: datetime.datetime | None = None

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> bool:
        """True — запущено этим вызовом, False — уже было запущено."""
        if self.is_running():
            return False
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(self._log_path, "a", encoding="utf-8")
        self._process = subprocess.Popen(
            self._command,
            cwd=str(self._cwd),
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        self._started_at = datetime.datetime.now(datetime.timezone.utc)
        return True

    def stop(self, timeout: float = 15) -> bool:
        """True — остановлено этим вызовом, False — уже было остановлено."""
        if not self.is_running():
            return False
        self._process.terminate()
        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=5)
        self._process = None
        return True

    @property
    def started_at(self) -> datetime.datetime | None:
        return self._started_at

    def tail_log(self, lines: int = 20) -> str:
        if not self._log_path.exists():
            return "(лога пока нет)"
        content = self._log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(content[-lines:]) or "(лог пуст)"
