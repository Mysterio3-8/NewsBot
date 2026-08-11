"""Память и хранение: слот тяжёлой работы, сжатие ротированных логов.

Замер на проде 2026-08-11 (консоль провайдера): 961 МБ памяти, 1254 МБ в свопе,
диск занят на 63%. То есть сервер лёг не от диска, а от того, что тяжёлые джобы
накладывались друг на друга.
"""
from __future__ import annotations

import gzip
import logging
from logging.handlers import RotatingFileHandler

from app.core.maintenance.workload import MediaWorkGuard
from app.logging_setup import _compress_rotated, _gz_name


def test_second_heavy_job_skips_the_tick():
    guard = MediaWorkGuard()

    with guard.slot("фильм") as first:
        assert first is True
        with guard.slot("клипы") as second:
            assert second is False


def test_slot_frees_up_after_the_job():
    guard = MediaWorkGuard()

    with guard.slot("фильм"):
        pass

    with guard.slot("клипы") as taken:
        assert taken is True


def test_slot_frees_up_even_after_a_crash():
    """Иначе одна ошибка в фильме навсегда остановила бы клипы и публикацию."""
    guard = MediaWorkGuard()

    try:
        with guard.slot("фильм"):
            raise RuntimeError("ffmpeg упал")
    except RuntimeError:
        pass

    assert guard.busy_with is None
    with guard.slot("клипы") as taken:
        assert taken is True


def test_skipped_job_does_not_hold_the_slot():
    """Пропустивший тик джоб не должен занять слот собой — иначе он заблокировал бы
    того, кто в этот момент реально работает."""
    guard = MediaWorkGuard()

    with guard.slot("фильм"):
        with guard.slot("клипы"):
            pass
        assert guard.busy_with == "фильм"


def test_rotated_log_is_stored_compressed(tmp_path):
    source = tmp_path / "app.log.1"
    source.write_text("строка лога\n" * 100, encoding="utf-8")
    dest = tmp_path / "app.log.1.gz"

    _compress_rotated(str(source), str(dest))

    assert not source.exists()
    assert gzip.open(dest, "rt", encoding="utf-8").read().startswith("строка лога")
    assert dest.stat().st_size < 1000  # текст жмётся более чем в десять раз


def test_handler_rotates_into_gz_and_keeps_backup_count(tmp_path):
    """Namer и rotator обязаны быть согласованы: сдвиг бэкапов идёт по именам от namer,
    и без него архивы копились бы вечно — «сжатие» обернулось бы утечкой диска."""
    handler = RotatingFileHandler(tmp_path / "app.log", maxBytes=200, backupCount=2)
    handler.namer = _gz_name
    handler.rotator = _compress_rotated
    logger = logging.getLogger("rotate-test")
    logger.propagate = False
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    for i in range(200):
        logger.info("строка номер %d, достаточно длинная чтобы добить до лимита", i)
    handler.close()

    archives = sorted(p.name for p in tmp_path.iterdir() if p.name.endswith(".gz"))
    assert archives == ["app.log.1.gz", "app.log.2.gz"]
