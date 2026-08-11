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
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("app")

DISK_WARN_PERCENT = 85
"""Порог, после которого владельцу уходит предупреждение.

Между 85% и «всё встало» остаётся примерно один фильм — то есть время среагировать."""

DISK_CRITICAL_PERCENT = 92
"""Порог аварийной уборки: чистим невзирая на сроки хранения.

Уборка по возрасту одна диск не спасает. Фильм весит 500–650 МБ и качается за минуты,
а порог `daily_video` — 6 часов: диск успевает забиться файлами, которые уборке ещё
«рано» трогать. Ровно так он и дошёл до 94% в июле."""

FILM_REQUIRED_FREE_MB = 2500
"""Сколько места требовать ПЕРЕД скачиванием фильма.

Фильм 650 МБ + нарезка клипов + запас на временные файлы ffmpeg. Скачивать в упор —
это гарантированно недокачанный файл и мусор, который потом ещё и убирать."""

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


@dataclass(frozen=True)
class DiskStatus:
    """Состояние раздела, на котором лежат временные файлы."""

    total_bytes: int
    free_bytes: int

    @property
    def used_percent(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return (self.total_bytes - self.free_bytes) / self.total_bytes * 100

    @property
    def free_mb(self) -> float:
        return self.free_bytes / (1024 * 1024)

    def __str__(self) -> str:
        return f"{self.used_percent:.0f}% занято, свободно {self.free_mb:.0f} МБ"


def disk_status(path: Path) -> DiskStatus:
    """Сколько места на разделе. Путь недоступен → «всё свободно».

    Fail-open осознанно: эта функция стоит гейтом перед скачиванием фильма, и ошибка
    статистики не повод останавливать публикации — реальную нехватку места поймает
    сама запись файла."""
    try:
        usage = shutil.disk_usage(path)
    except OSError as error:
        logger.warning("Не удалось прочитать статистику диска %s: %s", path, error)
        return DiskStatus(total_bytes=0, free_bytes=0)
    return DiskStatus(total_bytes=usage.total, free_bytes=usage.free)


def has_free_space(path: Path, required_mb: float) -> bool:
    """Хватит ли места под задачу. Статистика недоступна → считаем, что хватит."""
    status = disk_status(path)
    if status.total_bytes == 0:
        return True
    return status.free_mb >= required_mb


def emergency_cleanup(
    output_dir: Path, *, target_percent: float = DISK_CRITICAL_PERCENT
) -> CleanupResult:
    """Чистка ПО НЕХВАТКЕ МЕСТА: сносим самые старые временные файлы, игнорируя сроки.

    Обычная уборка смотрит только на возраст, и этого мало: фильм на 650 МБ приезжает
    за минуты, а трогать его «рано» ещё шесть часов. Здесь сортируем всё временное по
    времени изменения и удаляем с самого старого, пока не опустимся ниже порога.

    Удаляем только из каталогов `MEDIA_RETENTION_HOURS` — это заведомо промежуточные
    файлы. Ни БД, ни логи, ни ассеты сюда не попадают: аварийная уборка не должна уметь
    удалить то, что восстановить нельзя."""
    if disk_status(output_dir).used_percent < target_percent:
        return CleanupResult(removed_files=0, freed_bytes=0)

    candidates: list[Path] = []
    for name in MEDIA_RETENTION_HOURS:
        directory = output_dir / name
        if directory.exists():
            candidates.extend(path for path in directory.rglob("*") if path.is_file())

    removed = 0
    freed = 0
    for path in sorted(candidates, key=_mtime_or_zero):
        if disk_status(output_dir).used_percent < target_percent:
            break
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError as error:
            logger.warning("Аварийная уборка: не удалось удалить %s: %s", path, error)
            continue
        removed += 1
        freed += size

    for name in MEDIA_RETENTION_HOURS:
        directory = output_dir / name
        if directory.exists():
            _remove_empty_dirs(directory)

    result = CleanupResult(removed_files=removed, freed_bytes=freed)
    if removed:
        logger.warning(
            "Аварийная уборка диска: удалено %d файлов, освобождено %.0f МБ (%s)",
            removed, result.freed_mb, disk_status(output_dir),
        )
    return result


def _mtime_or_zero(path: Path) -> float:
    """Файл, исчезнувший между листингом и сортировкой, считаем самым старым —
    попытка его удалить всё равно отработает безопасно."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


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
