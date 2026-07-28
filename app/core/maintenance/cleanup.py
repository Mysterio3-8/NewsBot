"""Автоуборка временных медиафайлов (ТЗ 2026-07-28: «ничего не хранить никогда»).

Диск VPS забился до 94% и уронил публикацию: `tg_raw_media` копил 1220 файлов (2.9 ГБ),
`vk_raw_media` — 1272, а в `daily_video` лежали три недоудалённых фильма по 500-650 МБ.
Фильмы оставались от процессов, убитых OOM: удаление стоит в `finally`, который при
SIGKILL не выполняется.

Медиа нужны только до момента публикации поста, дальше это мусор. Уборка идёт по
возрасту файла, а не по статусу поста: файл старше порога заведомо пережил свой пост
(публикация укладывается в часы, не в дни), а привязка к статусу требовала бы обхода
всей БД на каждый файл.
"""
from __future__ import annotations

import datetime
import logging
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("app")

# Каталоги временных медиа и срок жизни в часах. Скачанные исходники постов живут
# сутки (пост публикуется в тот же день), фильмы — 6 часов (репост идёт сразу после
# скачивания; всё, что старше, — сирота от убитого процесса).
MEDIA_RETENTION_HOURS: dict[str, int] = {
    "tg_raw_media": 24,
    "vk_raw_media": 24,
    "raw": 24,
    "images": 24,
    "videos": 24,
    "daily_video": 6,
    "clips": 48,  # клипы ждут своей очереди публикации по плану (до суток)
}


@dataclass(frozen=True)
class CleanupResult:
    removed_files: int
    freed_bytes: int

    @property
    def freed_mb(self) -> float:
        return self.freed_bytes / (1024 * 1024)


def is_expired(path: Path, max_age_hours: int, now: float) -> bool:
    """Файл старше порога. Ошибка stat (файл исчез между листингом и проверкой) —
    считаем неистёкшим: удалять нечего."""
    try:
        age_seconds = now - path.stat().st_mtime
    except OSError:
        return False
    return age_seconds > max_age_hours * 3600


def cleanup_directory(directory: Path, max_age_hours: int, *, now: float | None = None) -> CleanupResult:
    """Удалить из каталога файлы старше порога. Пустые подкаталоги после этого тоже
    убираются — иначе `output/images/<post_id>/` копит десятки тысяч пустых папок."""
    if not directory.exists():
        return CleanupResult(removed_files=0, freed_bytes=0)

    now = now if now is not None else time.time()
    removed = 0
    freed = 0
    for path in directory.rglob("*"):
        if not path.is_file() or not is_expired(path, max_age_hours, now):
            continue
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError as error:
            logger.warning("Уборка: не удалось удалить %s: %s", path, error)
            continue
        removed += 1
        freed += size

    _remove_empty_dirs(directory)
    return CleanupResult(removed_files=removed, freed_bytes=freed)


def cleanup_output(output_dir: Path, *, now: float | None = None) -> CleanupResult:
    """Пройти все каталоги временных медиа по их срокам хранения."""
    total_removed = 0
    total_freed = 0
    for name, max_age_hours in MEDIA_RETENTION_HOURS.items():
        result = cleanup_directory(output_dir / name, max_age_hours, now=now)
        total_removed += result.removed_files
        total_freed += result.freed_bytes

    result = CleanupResult(removed_files=total_removed, freed_bytes=total_freed)
    if result.removed_files:
        logger.info(
            "Уборка диска: удалено %d файлов, освобождено %.0f МБ",
            result.removed_files, result.freed_mb,
        )
    return result


def _remove_empty_dirs(directory: Path) -> None:
    # Снизу вверх: сначала листья, потом опустевшие родители.
    for path in sorted(directory.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if not path.is_dir():
            continue
        try:
            next(path.iterdir())
        except StopIteration:
            path.rmdir()
        except OSError:
            continue


def format_disk_report(output_dir: Path) -> str:
    """Человеческая сводка для команды бота: сколько занимает каждый каталог."""
    lines = []
    for name in MEDIA_RETENTION_HOURS:
        directory = output_dir / name
        if not directory.exists():
            continue
        size = sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())
        count = sum(1 for p in directory.rglob("*") if p.is_file())
        lines.append(f"{name}: {size / (1024 * 1024):.0f} МБ ({count} файлов)")
    return "\n".join(lines) or "временных файлов нет"


def next_cleanup_time(now: datetime.datetime, interval_hours: int = 1) -> datetime.datetime:
    return now + datetime.timedelta(hours=interval_hours)
