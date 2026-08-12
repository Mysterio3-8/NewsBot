"""Тревоги владельцу в Telegram: место на диске.

Зачем отдельный отправитель, а не публикатор постов: тревога должна дойти даже когда
пайплайн публикации не работает — именно тогда она и нужна. Поэтому здесь голый вызов
Bot API через `requests`, без aiogram, без очереди и без БД постов.

Почему вообще: 2026-07-28 диск дошёл до 94% и уронил публикации; 2026-08-11 сервер
перестал пускать по SSH, и предполагаемая причина та же — а узнать об этом можно было
только по пропавшим постам, то есть постфактум. Софт видит своё место сам, значит
сказать о проблеме он обязан сам.
"""
from __future__ import annotations

import datetime
import logging
import os

from app.core.maintenance.cleanup import DISK_WARN_PERCENT, DiskStatus
from app.db.repository import Repository

logger = logging.getLogger("app")

TOKEN_ENV = "CONTROL_BOT_TOKEN"
OWNER_SETTING_KEY = "control_bot_owner_id"
OWNER_ENV = "CONTROL_BOT_OWNER_ID"

LAST_DISK_ALERT_KEY = "last_disk_alert_at"
LAST_SILENCE_ALERT_KEY = "last_silence_alert_at"
ALERT_COOLDOWN_HOURS = 6
"""Пауза между повторами одной и той же тревоги.

Джоб уборки ходит раз в час, а забитый диск сам не рассасывается — без паузы владелец
получал бы одно и то же сообщение каждый час и перестал бы их читать."""


def owner_chat_id(repo: Repository) -> int | None:
    """Владелец: сначала явный env, потом тот, кто первым нажал /start."""
    raw = os.environ.get(OWNER_ENV) or repo.get_setting(OWNER_SETTING_KEY)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("Владелец бота задан не числом: %r", raw)
        return None


def send_alert(repo: Repository, text: str) -> bool:
    """Отправить тревогу владельцу. False — отправить не удалось или некому.

    Никогда не поднимает исключение: тревога вызывается из служебного джоба, и падение
    отправки не должно ронять уборку диска, ради которой всё и затевалось."""
    token = os.environ.get(TOKEN_ENV)
    chat_id = owner_chat_id(repo)
    if not token or chat_id is None:
        logger.info("Тревога не отправлена: нет токена бота или владельца")
        return False

    try:
        import requests

        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=20,
        )
        if response.status_code != 200:
            logger.warning("Тревога не доставлена: HTTP %s", response.status_code)
            return False
    except Exception as error:  # noqa: BLE001 — граница сети, тревога не критичнее уборки
        logger.warning("Тревога не доставлена: %s", error)
        return False
    return True


def should_alert(repo: Repository, now: datetime.datetime, key: str = LAST_DISK_ALERT_KEY) -> bool:
    """Прошла ли пауза с прошлой такой же тревоги. Метка битая → шлём."""
    raw = repo.get_setting(key)
    if not raw:
        return True
    try:
        last = datetime.datetime.fromisoformat(raw)
    except ValueError:
        return True
    return (now - last) >= datetime.timedelta(hours=ALERT_COOLDOWN_HOURS)


def mark_alerted(repo: Repository, now: datetime.datetime, key: str = LAST_DISK_ALERT_KEY) -> None:
    repo.set_setting(key, now.isoformat())


def build_disk_alert(status: DiskStatus, freed_mb: float) -> str:
    """Текст тревоги. Пишем и то, что уборка уже сделала, — иначе непонятно, надо ли
    бежать к серверу прямо сейчас."""
    lines = [
        f"⚠️ Мало места на диске: {status.used_percent:.0f}% занято, "
        f"свободно {status.free_mb:.0f} МБ.",
    ]
    if freed_mb > 0:
        lines.append(f"Автоуборка освободила {freed_mb:.0f} МБ — этого не хватило.")
    lines.append(
        "Забитый диск останавливает фильмы и загрузку медиа в VK. "
        "Проверить: /disk в боте, дальше — журналы systemd и каталоги других софтов."
    )
    return "\n".join(lines)


def alert_once(repo: Repository, text: str, key: str, now: datetime.datetime | None = None) -> bool:
    """Отправить тревогу не чаще, чем раз в `ALERT_COOLDOWN_HOURS`.

    Пауза обязательна: сторожевые джобы ходят по расписанию, а поломка сама не
    рассасывается — без паузы владелец получал бы одно и то же сообщение каждый час и
    перестал бы их читать вовсе, что хуже, чем не слать их совсем."""
    now = now or datetime.datetime.utcnow()
    if not should_alert(repo, now, key):
        return False
    if not send_alert(repo, text):
        return False
    mark_alerted(repo, now, key)
    return True


def check_disk_and_alert(
    repo: Repository,
    status: DiskStatus,
    freed_mb: float,
    *,
    now: datetime.datetime | None = None,
    warn_percent: float = DISK_WARN_PERCENT,
) -> bool:
    """Предупредить владельца, если места мало. True — тревога отправлена."""
    if status.total_bytes == 0 or status.used_percent < warn_percent:
        return False
    now = now or datetime.datetime.utcnow()
    if not should_alert(repo, now):
        return False
    if not send_alert(repo, build_disk_alert(status, freed_mb)):
        return False
    mark_alerted(repo, now)
    logger.warning("Владельцу отправлена тревога о диске: %s", status)
    return True
