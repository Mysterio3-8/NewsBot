"""Контракт менеджера ↔ внешний софт.

Стандартный формат, которым менеджер правит настройки любого софта ИЗВНЕ, не зная его
внутренней схемы. Менеджер хранит контракт в своём реестре (`SoftRecord.config_json`) и
умеет отрендерить его в файл `manager_contract.yaml` в каталоге софта. Каждый софт
позже читает этот файл и применяет к своему config — так «полное управление» работает,
а проекты остаются независимыми (общий только формат, не код).

Пока покрыты антибан-лимиты (главное для всех автопостеров). Источники/каналы —
следующий слой, добавляются полями сюда же.
"""
from __future__ import annotations

import dataclasses
import json

import yaml

CONTRACT_FILENAME = "manager_contract.yaml"


@dataclasses.dataclass(frozen=True)
class SoftContract:
    """Управляемые извне настройки софта. None = «не задано, софт решает сам»."""

    max_posts_per_day: int | None = None
    min_interval_minutes: int | None = None
    max_interval_minutes: int | None = None
    quiet_start_hour: int | None = None
    quiet_end_hour: int | None = None
    sources_primary: tuple[str, ...] = ()
    """Источники основного потока софта.

    ⚠️ Имя нарочно НЕЙТРАЛЬНОЕ, а не «каналы YouTube» или «запросы SoundCloud»: менеджер
    не знает и не должен знать внутреннюю схему софта — в этом весь смысл контракта.
    Сопоставление делает сам софт (у Музыки primary → треки, secondary → сборники;
    у Минусов primary → YouTube-канал, secondary не используется)."""
    sources_secondary: tuple[str, ...] = ()

    @classmethod
    def from_config_json(cls, raw: str | None) -> "SoftContract":
        if not raw:
            return cls()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return cls()
        limits = data.get("limits", {}) if isinstance(data, dict) else {}
        sources = data.get("sources", {}) if isinstance(data, dict) else {}
        return cls(
            max_posts_per_day=limits.get("max_posts_per_day"),
            min_interval_minutes=limits.get("min_interval_minutes"),
            max_interval_minutes=limits.get("max_interval_minutes"),
            quiet_start_hour=limits.get("quiet_start_hour"),
            quiet_end_hour=limits.get("quiet_end_hour"),
            sources_primary=tuple(sources.get("primary") or ()),
            sources_secondary=tuple(sources.get("secondary") or ()),
        )

    LIMIT_FIELDS = (
        "max_posts_per_day",
        "min_interval_minutes",
        "max_interval_minutes",
        "quiet_start_hour",
        "quiet_end_hour",
    )

    def to_config_dict(self) -> dict:
        """Только заданные поля — чтобы не забивать реестр None'ами и пустыми списками."""
        limits = {
            name: getattr(self, name)
            for name in self.LIMIT_FIELDS
            if getattr(self, name) is not None
        }
        sources = {
            key: list(value)
            for key, value in (
                ("primary", self.sources_primary),
                ("secondary", self.sources_secondary),
            )
            if value
        }
        result: dict = {}
        if limits:
            result["limits"] = limits
        if sources:
            result["sources"] = sources
        return result

    def to_config_json(self) -> str:
        return json.dumps(self.to_config_dict(), ensure_ascii=False)

    def is_empty(self) -> bool:
        return self.to_config_dict() == {}

    def with_source(self, url: str, *, secondary: bool = False) -> "SoftContract":
        """Копия контракта с добавленным источником. Дубликат не добавляется дважды."""
        field = "sources_secondary" if secondary else "sources_primary"
        current = getattr(self, field)
        if url in current:
            return self
        return dataclasses.replace(self, **{field: current + (url,)})

    def without_source(self, url: str, *, secondary: bool = False) -> "SoftContract":
        """Копия контракта без источника.

        ⚠️ ПОСЛЕДНИЙ источник не удаляется: пустой список означает «источников нет», и
        софт молча перестанет находить контент. Ошибка была бы тихой — владелец увидел бы
        её только через сутки по пустой стене."""
        field = "sources_secondary" if secondary else "sources_primary"
        current = getattr(self, field)
        if url not in current or len(current) <= 1:
            return self
        return dataclasses.replace(
            self, **{field: tuple(item for item in current if item != url)}
        )

    def render_yaml(self) -> str:
        """Содержимое manager_contract.yaml для каталога софта."""
        header = (
            "# Управляется ботом-менеджером (📦 Софты). Правь из бота, не руками —\n"
            "# файл перезаписывается. Софт читает limits и применяет к своему расписанию.\n"
        )
        body = yaml.safe_dump(
            self.to_config_dict() or {"limits": {}, "sources": {}},
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
        return header + body

    def render_summary(self) -> str:
        """Человекочитаемая сводка лимитов для карточки софта в боте."""
        if self.is_empty():
            return "Лимиты не заданы (софт решает сам)."
        lines = []
        if self.max_posts_per_day is not None:
            lines.append(f"📅 Лимит/день: {self.max_posts_per_day}")
        if self.min_interval_minutes is not None:
            interval = str(self.min_interval_minutes)
            if self.max_interval_minutes is not None:
                interval = f"{self.min_interval_minutes}–{self.max_interval_minutes}"
            lines.append(f"⏱ Интервал: {interval} мин")
        if self.quiet_start_hour is not None and self.quiet_end_hour is not None:
            lines.append(f"🌙 Ночная пауза: {self.quiet_start_hour}:00–{self.quiet_end_hour}:00 МСК")
        if self.sources_primary:
            lines.append(f"📥 Источников: {len(self.sources_primary)}")
        if self.sources_secondary:
            lines.append(f"📥 Второй поток: {len(self.sources_secondary)}")
        return "\n".join(lines)


def contract_file_path(project_path: str) -> str:
    from pathlib import Path

    return str(Path(project_path) / CONTRACT_FILENAME)


def write_contract_file(project_path: str, contract: SoftContract) -> str:
    """Пишет manager_contract.yaml в каталог софта, возвращает путь. Вызывать явно
    (не автоматически) — это запись в чужой проект."""
    path = contract_file_path(project_path)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(contract.render_yaml())
    return path
