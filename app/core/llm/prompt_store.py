"""Переопределения промптов (текстовых шаблонов) из бота.

Промпты живут файлами в `prompts/*.txt`, но deploy.sh синкает эту папку с диска —
правка файла на сервере затиралась бы при следующем коммите. Поэтому изменённый из
бота текст хранится в БД (таблица settings), а файл остаётся дефолтом: правка
переживает деплой, а «Сбросить» возвращает заводской текст.

Порядок разрешения: БД → файл. Пустая строка в БД = «сброшено», не «пустой промпт».
"""
from __future__ import annotations

import logging

logger = logging.getLogger("app")

SETTING_PREFIX = "prompt_override:"

# Какие шаблоны показывать в боте, с человеческими именами. Ключ — имя файла в prompts/.
EDITABLE_PROMPTS: dict[str, str] = {
    "rewrite": "📰 Рерайт новости",
    "rewrite_kino": "🎬 Рерайт кино",
    "system": "⚙️ Системный (общий стиль)",
    "headline": "🔤 Заголовок-хук",
    "clip_hook": "✂️ Надпись на клипе",
    "video_title": "🎬 Название фильма",
    "video_description": "🎬 Описание фильма",
    "classifier": "🔍 Фильтр новостей",
    "image_query": "🖼 Запрос картинки",
    "movie_query": "🖼 Запрос кадра фильма",
}


def setting_key(name: str) -> str:
    return f"{SETTING_PREFIX}{name}"


def get_override(repo, name: str) -> str | None:
    """Текст шаблона из БД либо None, если владелец его не менял."""
    if repo is None:
        return None
    try:
        raw = repo.get_setting(setting_key(name))
    except Exception:  # БД недоступна — не роняем публикацию, берём файл
        logger.exception("Не удалось прочитать переопределение промпта %s", name)
        return None
    return raw or None


def set_override(repo, name: str, text: str) -> None:
    repo.set_setting(setting_key(name), text)
    logger.info("Промпт %s переопределён из бота (%d символов)", name, len(text))


def reset_override(repo, name: str) -> None:
    """Вернуть заводской текст — пустая строка означает «нет переопределения»."""
    repo.set_setting(setting_key(name), "")
    logger.info("Промпт %s сброшен к заводскому", name)


def is_overridden(repo, name: str) -> bool:
    return get_override(repo, name) is not None
