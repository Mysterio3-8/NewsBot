"""Доступ к БД-реестру менеджера. Отдельный файл data/manager.db + своя self-healing
миграция (та же идея, что в app/db/repository.py) — чтобы схема Новостей не влияла."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.manager.models import ManagerBase, SoftRecord
from app.paths import DATA_DIR

DEFAULT_MANAGER_DB_PATH = DATA_DIR / "manager.db"


def make_manager_engine(db_path: Path | None = None):
    path = db_path or DEFAULT_MANAGER_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}")


def init_manager_db(engine) -> None:
    """create_all + долив недостающих колонок (лёгкая миграция без Alembic)."""
    ManagerBase.metadata.create_all(engine)
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.connect() as connection:
        for table in ManagerBase.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                column_type = column.type.compile(engine.dialect)
                connection.execute(
                    text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column_type}")
                )
        connection.commit()


class ManagerRepository:
    def __init__(self, engine) -> None:
        self._session_factory: sessionmaker[Session] = sessionmaker(bind=engine)

    def list_softs(self) -> list[SoftRecord]:
        with self._session_factory() as session:
            return session.query(SoftRecord).order_by(SoftRecord.sort_order, SoftRecord.id).all()

    def get_soft(self, soft_id: str) -> SoftRecord | None:
        with self._session_factory() as session:
            return session.query(SoftRecord).filter(SoftRecord.soft_id == soft_id).first()

    def upsert_soft(self, soft_id: str, **fields) -> SoftRecord:
        """Создать или обновить софт по стабильному soft_id (идемпотентно для сида)."""
        with self._session_factory() as session:
            record = session.query(SoftRecord).filter(SoftRecord.soft_id == soft_id).first()
            if record is None:
                record = SoftRecord(soft_id=soft_id, **fields)
                session.add(record)
            else:
                for key, value in fields.items():
                    setattr(record, key, value)
            session.commit()
            session.refresh(record)
            return record

    def set_enabled(self, soft_id: str, enabled: bool) -> None:
        with self._session_factory() as session:
            session.query(SoftRecord).filter(SoftRecord.soft_id == soft_id).update(
                {"enabled": enabled}
            )
            session.commit()

    def update_config(self, soft_id: str, config: dict) -> None:
        with self._session_factory() as session:
            session.query(SoftRecord).filter(SoftRecord.soft_id == soft_id).update(
                {"config_json": json.dumps(config, ensure_ascii=False)}
            )
            session.commit()

    def delete_soft(self, soft_id: str) -> None:
        with self._session_factory() as session:
            session.query(SoftRecord).filter(SoftRecord.soft_id == soft_id).delete()
            session.commit()


# Стартовый набор внешних софтов (все на VPS по словам владельца 2026-07-19). Пути к
# проектам и способ управления (systemd-юнит/команда) на сервере пока неизвестны —
# заполняются позже, тогда же включится реальный старт/стоп. Сейчас реестр даёт список.
# Юниты выяснены разведкой VPS 2026-07-21 (`systemctl list-units`), не угаданы:
# Минусы — не демон, а ежедневный ТАЙМЕР (сам .service `static`, живёт секунды),
# поэтому включаем/выключаем .timer. Музыка — 9 юнитов (7 сервисов + 2 таймера).
# Природа и Shorts на VPS НЕ развёрнуты (нет ни каталога, ни юнитов) → host=local.
MUSIC_UNITS = json.dumps([
    "tg-music-bot.service",
    "tg-music-api.service",
    "tg-music-worker.service",
    "tg-music-youtube.service",
    "tg-music-youtube-user.service",
    "tg-music-soundcloud.service",
    "tg-music-telegram-channel.service",
    "tg-music-youtube-scan.timer",
    "tg-music-telegram-channel-scan.timer",
])

DEFAULT_SOFTS: tuple[dict, ...] = (
    {"soft_id": "p_minus", "title": "➖ Минусы (YT→VK)", "host": "vps", "sort_order": 10,
     "systemd_units_json": json.dumps(["yt-vk-publisher.timer"])},
    {"soft_id": "p_music", "title": "🎵 Музыка (TG)", "host": "vps", "sort_order": 20,
     "systemd_units_json": MUSIC_UNITS},
    {"soft_id": "p_nature", "title": "🌿 Природа (VK)", "host": "local", "sort_order": 30,
     "path_env": "NATURE_BOT_PATH"},
    {"soft_id": "p_shorts", "title": "🎬 Shorts", "host": "local", "sort_order": 40,
     "path_env": "SHORTS_PATH"},
)


def seed_default_softs(repo: ManagerRepository) -> None:
    """Идемпотентно заводит известные внешние софты. Обновляет title/path_env/order,
    но НЕ трогает enabled/config (их владелец правит из бота)."""
    for spec in DEFAULT_SOFTS:
        soft_id = spec["soft_id"]
        fields = {k: v for k, v in spec.items() if k != "soft_id"}
        if repo.get_soft(soft_id) is None:
            repo.upsert_soft(soft_id, kind="process", **fields)
        else:
            repo.upsert_soft(soft_id, **fields)  # обновляем только метаданные списка
