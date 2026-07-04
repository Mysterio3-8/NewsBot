"""Настройка логирования с ротацией (раздел 17 SPEC.md)."""
from __future__ import annotations

import logging
import time
from logging.handlers import RotatingFileHandler

from app.config.loader import LoggingConfig
from app.paths import LOGS_DIR

MAX_LOG_AGE_DAYS = 30

LOG_FILES = {
    "app": "app.log",
    "llm": "llm.log",
    "monitoring": "monitoring.log",
    "publishing": "publishing.log",
    "errors": "errors.log",
}


def _make_handler(filename: str, config: LoggingConfig, level: int) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        LOGS_DIR / filename,
        maxBytes=config.max_file_size_mb * 1024 * 1024,
        backupCount=config.backup_count,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    return handler


def cleanup_old_logs(max_age_days: int = MAX_LOG_AGE_DAYS) -> int:
    """Удаляет файлы логов (включая ротированные бэкапы) старше max_age_days. Возвращает число удалённых."""
    if not LOGS_DIR.exists():
        return 0

    cutoff_timestamp = time.time() - max_age_days * 86400
    deleted_count = 0
    for path in LOGS_DIR.iterdir():
        if path.is_file() and path.stat().st_mtime < cutoff_timestamp:
            path.unlink()
            deleted_count += 1
    return deleted_count


def setup_logging(config: LoggingConfig) -> None:
    """Идемпотентно: вызывается и в control_bot.main(), и внутри ServiceController.start()
    (когда срабатывает AUTOSTART_SERVICE) — без этой защиты хендлеры добавлялись дважды
    и каждая строка лога писалась в файл два раза (обнаружено на проде 2026-07-04)."""
    root = logging.getLogger()
    if root.handlers:
        return

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, config.level.upper(), logging.INFO)
    root.setLevel(level)
    root.addHandler(_make_handler(LOG_FILES["app"], config, level))
    root.addHandler(_make_handler(LOG_FILES["errors"], config, logging.ERROR))

    for logger_name in ("llm", "monitoring", "publishing"):
        logger = logging.getLogger(logger_name)
        logger.addHandler(_make_handler(LOG_FILES[logger_name], config, level))

    cleanup_old_logs()
