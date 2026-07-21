"""Модель реестра софтов. Отдельный Base (своя БД-файл data/manager.db) — чтобы
таблица менеджера НИКАК не пересекалась со схемой Новостей."""
from __future__ import annotations

import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class ManagerBase(DeclarativeBase):
    pass


class SoftRecord(ManagerBase):
    """Один внешний софт в реестре менеджера. Каналы Новостей и движок в этой таблице
    НЕ хранятся (они живут в БД Новостей / виртуальны) — здесь только самостоятельные
    проекты (Природа, Минусы, Музыка, Shorts…), чтобы не дублировать состояние каналов.

    Секреты не храним — только ИМЯ env-переменной с путём (инвариант проекта).
    Управление процессом (старт/стоп) — следующий срез; сейчас реестр даёт список
    и настройки. systemd_unit/path_env/command_json — заготовка под этот срез."""

    __tablename__ = "softs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    soft_id: Mapped[str] = mapped_column(String, unique=True)  # стабильный ключ, напр. "p_nature"
    title: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, default="process")
    host: Mapped[str] = mapped_column(String, default="vps")  # где живёт софт
    path_env: Mapped[str | None] = mapped_column(String, nullable=True)  # имя env с путём проекта
    command_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # argv как JSON-список
    # Список systemd-юнитов софта (JSON-массив). Софт = НАБОР юнитов: Музыка — 9 штук,
    # Минусы — один .timer (батч, не демон). Пусто → софт не под systemd-управлением.
    systemd_units_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # желаемое автосостояние
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    config_json: Mapped[str] = mapped_column(Text, default="{}")  # произвольные настройки софта
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
