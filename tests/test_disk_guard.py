"""Диск: аварийная уборка по нехватке места, гейт перед фильмом, тревога владельцу.

ТЗ владельца 2026-08-11: «решай проблемы чтобы диск не забивался». Уборки по срокам
недостаточно: фильм на 650 МБ приезжает за минуты, а трогать его «рано» ещё шесть часов.
"""
from __future__ import annotations

import datetime
import time

from app.core.maintenance import cleanup as cleanup_module
from app.core.maintenance.alerts import (
    LAST_DISK_ALERT_KEY,
    build_disk_alert,
    check_disk_and_alert,
    should_alert,
)
from app.core.maintenance.cleanup import (
    DiskStatus,
    disk_status,
    emergency_cleanup,
    has_free_space,
)


def _fill(directory, name: str, size: int, age_hours: float) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"x" * size)
    stamp = time.time() - age_hours * 3600
    import os

    os.utime(path, (stamp, stamp))


def _fake_disk(monkeypatch, percents: list[float]) -> None:
    """Занятость раздела по шагам: каждый вызов берёт следующее значение, последнее
    повторяется. Нужно, чтобы проверить именно ЦИКЛ «удалили — переспросили»."""
    values = list(percents)

    def fake(path):
        percent = values.pop(0) if len(values) > 1 else values[0]
        total = 1000
        return DiskStatus(total_bytes=total, free_bytes=int(total * (1 - percent / 100)))

    monkeypatch.setattr(cleanup_module, "disk_status", fake)


def test_disk_status_reports_used_percent():
    status = DiskStatus(total_bytes=1000, free_bytes=100)

    assert status.used_percent == 90.0


def test_unreadable_disk_is_treated_as_free(monkeypatch, tmp_path):
    """Fail-open: ошибка статистики не повод останавливать публикации — реальную
    нехватку места поймает сама запись файла."""

    def boom(path):
        raise OSError("нет доступа")

    monkeypatch.setattr(cleanup_module.shutil, "disk_usage", boom)

    assert disk_status(tmp_path).total_bytes == 0
    assert has_free_space(tmp_path, required_mb=100_000) is True


def test_emergency_cleanup_removes_files_younger_than_retention(tmp_path, monkeypatch):
    """Главное отличие от обычной уборки: срок хранения игнорируется.

    Фильм скачан 10 минут назад (порог — 6 часов), но места нет прямо сейчас."""
    _fill(tmp_path / "daily_video", "film.mp4", 500, age_hours=0.2)
    # Значения по шагам: гейт на входе, проверка перед удалением, проверка после него.
    _fake_disk(monkeypatch, [99.0, 99.0, 10.0])

    result = emergency_cleanup(tmp_path)

    assert result.removed_files == 1
    assert not (tmp_path / "daily_video" / "film.mp4").exists()


def test_emergency_cleanup_starts_with_the_oldest_file(tmp_path, monkeypatch):
    """Удаляем с самого старого: свежий файл с большой вероятностью ещё нужен посту."""
    _fill(tmp_path / "raw", "old.jpg", 100, age_hours=5)
    _fill(tmp_path / "raw", "new.jpg", 100, age_hours=0.1)
    _fake_disk(monkeypatch, [99.0, 99.0, 10.0])

    emergency_cleanup(tmp_path)

    assert not (tmp_path / "raw" / "old.jpg").exists()
    assert (tmp_path / "raw" / "new.jpg").exists()


def test_emergency_cleanup_does_nothing_when_there_is_room(tmp_path, monkeypatch):
    _fill(tmp_path / "raw", "keep.jpg", 100, age_hours=100)
    _fake_disk(monkeypatch, [40.0])

    result = emergency_cleanup(tmp_path)

    assert result.removed_files == 0
    assert (tmp_path / "raw" / "keep.jpg").exists()


def test_emergency_cleanup_touches_only_temporary_dirs(tmp_path, monkeypatch):
    """Аварийная уборка не должна уметь удалить то, что не восстановить: БД и логи."""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "app.db").write_bytes(b"x" * 900)
    _fake_disk(monkeypatch, [99.0])

    emergency_cleanup(tmp_path)

    assert (tmp_path / "data" / "app.db").exists()


def test_has_free_space_compares_against_requirement(monkeypatch):
    monkeypatch.setattr(
        cleanup_module,
        "disk_status",
        lambda p: DiskStatus(total_bytes=10 * 1024**3, free_bytes=1024**3),
    )

    assert has_free_space(object(), required_mb=500) is True
    assert has_free_space(object(), required_mb=5000) is False


class _Repo:
    def __init__(self, values: dict | None = None) -> None:
        self.values = values or {}

    def get_setting(self, key, default=None):
        return self.values.get(key, default)

    def set_setting(self, key, value):
        self.values[key] = value


def test_alert_is_sent_when_disk_is_above_the_threshold(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(
        "app.core.maintenance.alerts.send_alert", lambda repo, text: sent.append(text) or True
    )
    repo = _Repo()

    assert check_disk_and_alert(repo, DiskStatus(1000, 50), freed_mb=12) is True
    assert "Мало места" in sent[0]
    assert repo.values[LAST_DISK_ALERT_KEY]


def test_no_alert_while_there_is_room(monkeypatch):
    monkeypatch.setattr(
        "app.core.maintenance.alerts.send_alert",
        lambda repo, text: (_ for _ in ()).throw(AssertionError("слать нечего")),
    )

    assert check_disk_and_alert(_Repo(), DiskStatus(1000, 500), freed_mb=0) is False


def test_alert_is_not_repeated_every_hour(monkeypatch):
    """Джоб ходит раз в час, а забитый диск сам не рассасывается: без паузы владелец
    получал бы одно и то же сообщение каждый час и перестал бы их читать."""
    now = datetime.datetime(2026, 8, 11, 12, 0)
    repo = _Repo({LAST_DISK_ALERT_KEY: (now - datetime.timedelta(hours=1)).isoformat()})

    assert should_alert(repo, now) is False
    assert should_alert(repo, now + datetime.timedelta(hours=6)) is True


def test_alert_mentions_that_cleanup_already_tried():
    text = build_disk_alert(DiskStatus(1000, 50), freed_mb=300)

    assert "Автоуборка освободила 300 МБ" in text
