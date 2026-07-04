import logging
import os
import time

from app.config.loader import LoggingConfig
from app.logging_setup import cleanup_old_logs, setup_logging


def test_cleanup_old_logs_removes_only_stale_files(tmp_path, monkeypatch):
    import app.logging_setup as logging_setup_module

    monkeypatch.setattr(logging_setup_module, "LOGS_DIR", tmp_path)

    old_file = tmp_path / "app.log.5"
    old_file.write_text("старый лог")
    old_timestamp = time.time() - 31 * 86400
    os.utime(old_file, (old_timestamp, old_timestamp))

    fresh_file = tmp_path / "app.log"
    fresh_file.write_text("свежий лог")

    deleted_count = cleanup_old_logs(max_age_days=30)

    assert deleted_count == 1
    assert not old_file.exists()
    assert fresh_file.exists()


def test_cleanup_old_logs_returns_zero_when_dir_missing(tmp_path, monkeypatch):
    import app.logging_setup as logging_setup_module

    monkeypatch.setattr(logging_setup_module, "LOGS_DIR", tmp_path / "does_not_exist")

    assert cleanup_old_logs() == 0


def test_setup_logging_is_idempotent(tmp_path, monkeypatch):
    """Регрессия: control_bot.main() и ServiceController.start() оба вызывают
    setup_logging() в одном процессе (AUTOSTART_SERVICE) — без защиты хендлеры
    добавлялись дважды и каждая строка лога писалась в файл дважды (прод, 2026-07-04)."""
    import app.logging_setup as logging_setup_module

    monkeypatch.setattr(logging_setup_module, "LOGS_DIR", tmp_path)
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    root.handlers = []
    try:
        config = LoggingConfig(level="INFO", max_file_size_mb=1, backup_count=1)
        setup_logging(config)
        handlers_after_first_call = len(root.handlers)

        setup_logging(config)

        assert len(root.handlers) == handlers_after_first_call
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers = saved_handlers
