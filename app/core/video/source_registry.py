"""Добавление YouTube-источника видео из бота.

Зачем. Источники выгорают: каждая неудачная попытка помечает ролик взятым, и рано или
поздно софт честно пишет «все ролики источников уже публиковались». До сих пор лечение
было ручным — правка `seed_channels.py`, коммит, деплой, прогон сида. То есть простой
длился ровно столько, сколько я не за клавиатурой.

Теперь владелец кидает ссылку в бота, и канал получает источник сразу.

Чистые функции без aiogram: бот только передаёт текст и печатает ответ.
"""
from __future__ import annotations

import json
import re

from app.core.channel_settings import ChannelSettings
from app.db.models import Channel
from app.db.repository import Repository

YOUTUBE_URL = re.compile(
    r"^https?://(www\.)?(youtube\.com|m\.youtube\.com)/"
    r"(@[\w%.\-]+|c/[\w%.\-]+|channel/[\w\-]+|user/[\w%.\-]+)/?$",
    re.IGNORECASE,
)
"""Ссылка на КАНАЛ, а не на ролик.

Проверка нужна не ради строгости, а потому что ошибка тут дорогая и тихая: ссылку на
отдельное видео софт примет, будет ежедневно ходить по ней за списком роликов, получать
пустоту — и это выглядит как «источник добавили, а фильмов нет»."""


def normalize_source_url(text: str) -> str | None:
    """Текст из сообщения → пригодная ссылка на канал. None — не подходит.

    Хвост запроса (`?si=...`, `/videos`) срезаем: он приходит из «поделиться» и мешает
    сравнению с уже добавленными источниками."""
    url = (text or "").strip().split()[0] if (text or "").strip() else ""
    url = url.split("?")[0].rstrip("/")
    if url.endswith("/videos"):
        url = url[: -len("/videos")]
    return url if YOUTUBE_URL.match(url) else None


def video_channels(repo: Repository) -> list[Channel]:
    """Каналы, которые публикуют видео. Обычно ровно один — Кино."""
    result = []
    for channel in repo.list_channels():
        settings = ChannelSettings.from_json(channel.settings_json)
        if settings.daily_video_youtube_channels or settings.daily_video_group is not None:
            result.append(channel)
    return result


def add_video_source(repo: Repository, text: str) -> str:
    """Добавить источник всем видео-каналам. Возвращает ответ для бота.

    Пишем прямо в `settings_json`, а не в `seed_channels.py`: сид едет только с деплоем,
    а источник нужен здесь и сейчас. ⚠️ Обратная сторона — при следующем прогоне сида
    список источников перезапишется его версией, и добавленную ссылку надо будет
    перенести в код. Пока это дешевле простоя."""
    url = normalize_source_url(text)
    if url is None:
        return (
            "Это не похоже на ссылку на YouTube-канал.\n"
            "Нужна ссылка вида https://www.youtube.com/@ИмяКанала — именно на канал, "
            "а не на отдельное видео."
        )

    channels = video_channels(repo)
    if not channels:
        return "Не нашёл ни одного канала, который публикует видео."

    added: list[str] = []
    for channel in channels:
        settings = ChannelSettings.from_json(channel.settings_json)
        sources = list(settings.daily_video_youtube_channels)
        if url in sources:
            continue
        sources.append(url)
        data = json.loads(channel.settings_json or "{}")
        data["daily_video_youtube_channels"] = sources
        repo.update_channel(channel.id, settings_json=json.dumps(data, ensure_ascii=False))
        added.append(f"{channel.name} (источников стало {len(sources)})")

    if not added:
        return f"Этот источник уже добавлен: {url}"
    return "✅ Источник добавлен:\n" + url + "\n\n" + "\n".join(added)
