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
import re

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
    text_post_template: str | None = None
    """Шаблон текста публикации. None = софт берёт свой заводской.

    ⚠️ В шаблоне живут плейсхолдеры вида `{artist}`, `{title}`. Потерянный плейсхолдер
    ломает публикацию МОЛЧА — пост выходит с пустым местом вместо названия, и владелец
    узнаёт об этом по стене. Поэтому редактор обязан сверять набор плейсхолдеров с
    прежним значением и предупреждать (`missing_placeholders`)."""
    text_base_tags: tuple[str, ...] = ()
    text_channel_phrases: tuple[str, ...] = ()
    """Постоянные SEO-ключи сообщества. Не зависят от текста конкретного поста — см.
    разбор двух сортов ключей в `all_auto/SEO.md`."""
    genres: tuple[tuple[str, str], ...] = ()
    """Жанры кнопки «🎼 Сборник по жанру»: пары «имя кнопки → поисковый запрос».

    Пара, а не одна строка: на кнопке нужно короткое «Фонк», а искать надо «русский
    фонк» — иначе либо кнопка нечитаемая, либо выдача не та. Пусто = софт берёт свой
    список из `config.yaml`, ровно как до появления этой правки."""

    TEXT_FIELDS = {
        "template": "text_post_template",
        "tags": "text_base_tags",
        "phrases": "text_channel_phrases",
    }
    """Короткий ключ кнопки → поле. Короткий нужен, потому что callback_data Telegram
    ограничен 64 байтами, а в него уже входят префикс и soft_id."""

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
        texts = data.get("texts", {}) if isinstance(data, dict) else {}
        return cls(
            text_post_template=texts.get("post_template") or None,
            text_base_tags=tuple(texts.get("base_tags") or ()),
            text_channel_phrases=tuple(texts.get("channel_phrases") or ()),
            max_posts_per_day=limits.get("max_posts_per_day"),
            min_interval_minutes=limits.get("min_interval_minutes"),
            max_interval_minutes=limits.get("max_interval_minutes"),
            quiet_start_hour=limits.get("quiet_start_hour"),
            quiet_end_hour=limits.get("quiet_end_hour"),
            sources_primary=tuple(sources.get("primary") or ()),
            sources_secondary=tuple(sources.get("secondary") or ()),
            genres=cls._genres_from(data.get("genres") if isinstance(data, dict) else None),
        )

    @staticmethod
    def _genres_from(raw) -> tuple[tuple[str, str], ...]:
        """Список из реестра → пары. Битую запись пропускаем: контракт не должен уметь
        уронить бот, а один потерянный жанр владелец добавит заново."""
        if not isinstance(raw, list):
            return ()
        pairs = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            query = str(item.get("query", "")).strip() or name
            if name:
                pairs.append((name, query))
        return tuple(pairs)

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
        texts: dict = {}
        if self.text_post_template:
            texts["post_template"] = self.text_post_template
        if self.text_base_tags:
            texts["base_tags"] = list(self.text_base_tags)
        if self.text_channel_phrases:
            texts["channel_phrases"] = list(self.text_channel_phrases)
        result: dict = {}
        if limits:
            result["limits"] = limits
        if sources:
            result["sources"] = sources
        if texts:
            result["texts"] = texts
        if self.genres:
            result["genres"] = [{"name": name, "query": query} for name, query in self.genres]
        return result

    def with_genre(self, name: str, query: str = "") -> "SoftContract":
        """Копия контракта с добавленным (или переписанным) жанром.

        Повтор имени ЗАМЕНЯЕТ запрос, а не добавляет второй такой же жанр: две кнопки
        «Фонк» с разными запросами владелец различить не сможет."""
        name = (name or "").strip()
        query = (query or "").strip() or name
        if not name:
            return self
        others = tuple(
            pair for pair in self.genres if pair[0].casefold() != name.casefold()
        )
        return dataclasses.replace(self, genres=others + ((name, query),))

    def without_genre(self, name: str) -> "SoftContract":
        """Копия контракта без жанра. Последний удалять МОЖНО, в отличие от источников:
        пустой список жанров — это просто «кнопка ведёт к списку софта из config.yaml»,
        а не «софту негде брать контент»."""
        return dataclasses.replace(
            self,
            genres=tuple(
                pair for pair in self.genres if pair[0].casefold() != (name or "").strip().casefold()
            ),
        )

    def with_text(self, key: str, raw: str) -> "SoftContract":
        """Копия контракта с новым текстовым полем. Пустая строка снимает настройку —
        софт возвращается к своему заводскому значению из `config.yaml`."""
        field = self.TEXT_FIELDS[key]
        value = (raw or "").strip()
        if field == "text_post_template":
            return dataclasses.replace(self, text_post_template=value or None)
        items = tuple(
            part.strip() for part in value.replace("\n", ",").split(",") if part.strip()
        )
        return dataclasses.replace(self, **{field: items})

    def text_value(self, key: str) -> str:
        """Текущее значение поля одной строкой — для показа в боте."""
        value = getattr(self, self.TEXT_FIELDS[key])
        if isinstance(value, tuple):
            return ", ".join(value)
        return value or ""


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
        if self.text_post_template:
            lines.append("📝 Шаблон поста: свой")
        if self.text_base_tags or self.text_channel_phrases:
            total = len(self.text_base_tags) + len(self.text_channel_phrases)
            lines.append(f"🔎 SEO-ключей: {total}")
        if self.genres:
            lines.append(f"🎼 Жанров: {len(self.genres)}")
        return "\n".join(lines)


def missing_placeholders(old: str | None, new: str) -> list[str]:
    """Плейсхолдеры, которые были в прежнем шаблоне и пропали в новом.

    Потеря плейсхолдера — тихая поломка: публикация не падает, просто выходит без
    названия трека. Та же проверка уже стоит в редакторе шаблонов Новостей, и заведена
    она была ровно после такого случая."""
    if not old:
        return []
    pattern = re.compile(r"\{[a-zA-Z_]+\}")
    return [name for name in dict.fromkeys(pattern.findall(old)) if name not in new]


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
