"""Выбор и скачивание видео для ежедневного видео-репоста.

Источники: YouTube-канал (основной — запрос пользователя 2026-07-18: VK жёстко
троттлит видео-CDN для датацентр-IP VPS — реальная скорость падает до единиц КБ/с,
хотя обычный канал VPS отдаёт 200+ МБ/с; полная докачка фильма растягивалась бы на
многие часы) и VK-группа (оставлена как резервный путь, код рабочий).
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger("monitoring")

# Для нарезки клипов хватает среднего качества; выше 720p не качаем — фильм в
# максимальном качестве это гигабайты, а диск VPS маленький (уже забивался на 100%).
MAX_DOWNLOAD_HEIGHT = 720

_MP4_KEY = re.compile(r"^mp4_(\d+)$")


class VideoDownloadError(Exception):
    """Видео не удалось скачать ни прямой ссылкой, ни через yt-dlp."""


POT_SCRIPT_ENV = "YT_POT_SCRIPT"
DEFAULT_POT_SCRIPT = "/opt/bgutil-pot/server/build/generate_once.js"
JS_RUNTIMES = {"node": {}, "deno": {}}
"""Движки для решения n-challenge. По умолчанию yt-dlp ищет только Deno, а на сервере
стоит Node — отсюда и брались «форматы отсутствуют» при живых куках. Перечисляем оба:
yt-dlp берёт тот, что найдёт, и молча живёт дальше, если нет ни одного."""


def ytdlp_options(**overrides) -> dict[str, Any]:
    """Базовые опции yt-dlp: куки, PO-token провайдер и JS-движок.

    Три вещи, без которых YouTube с серверного IP не отдаёт видео ВООБЩЕ (разобрано
    живыми вызовами 2026-08-14, каждая проявлялась по-своему):

    1. **Куки** (`YT_COOKIES_FILE`) — иначе «Sign in to confirm you're not a bot».
       Файл машинно-специфичный, в git его нет никогда (инвариант: секреты вне git).
       ⚠️ Экспорт НЕавторизованной сессии выглядит как обычный cookies.txt, но не
       помогает: нужны `SID`/`HSID`/`APISID`/`LOGIN_INFO`. Куки залогиненного аккаунта
       повышают риск блокировки САМОГО аккаунта — брать одноразовый, не основной.
    2. **PO-token** (`YT_POT_SCRIPT`) — без него ответ приходит, но в нём только
       раскадровки (`sb0..sb3`), ни одного медиа-потока. Это и выглядело как «формат
       недоступен» на всех девяти каналах-источниках и всех player_client.
    3. **JS-движок** — n-challenge решается внешним скриптом (`yt-dlp-ejs`), и без
       движка формат-лист снова обрезается до раскадровок.

    Любой из трёх отсутствует — ходим как раньше: опция просто не выставляется, код не
    падает. Это важно для дев-машины, где ни провайдера, ни node может не быть."""
    options: dict[str, Any] = {"quiet": True, "no_warnings": True, "js_runtimes": JS_RUNTIMES}
    cookies_path = os.environ.get("YT_COOKIES_FILE")
    if cookies_path and Path(cookies_path).exists():
        options["cookiefile"] = cookies_path
    elif cookies_path:
        logger.warning("YT_COOKIES_FILE указывает на несуществующий файл: %s", cookies_path)

    pot_script = os.environ.get(POT_SCRIPT_ENV, DEFAULT_POT_SCRIPT)
    if pot_script and Path(pot_script).exists():
        options["extractor_args"] = {
            "youtubepot-bgutilscript": {"script_path": [pot_script]}
        }
    options.update(overrides)
    return options


@dataclass(frozen=True)
class SourceVideo:
    ref: str  # "ownerid_videoid" (VK) или "youtube_<id>" — ключ дедупа в reposted_videos
    title: str
    description: str
    duration_seconds: int
    direct_urls: dict[int, str]  # высота (480/720...) → прямая mp4-ссылка (только VK)
    page_url: str  # страница видео — вход для yt-dlp
    owner_id: int | None = None  # только VK
    video_id: int | None = None  # только VK


def source_video_from_item(item: dict[str, Any]) -> SourceVideo:
    """Сырое item из video.get → SourceVideo. Чистая функция — тестируется без сети."""
    owner_id = int(item["owner_id"])
    video_id = int(item["id"])
    direct_urls: dict[int, str] = {}
    for key, url in (item.get("files") or {}).items():
        match = _MP4_KEY.match(key)
        if match:
            direct_urls[int(match.group(1))] = url
    return SourceVideo(
        ref=f"{owner_id}_{video_id}",
        owner_id=owner_id,
        video_id=video_id,
        title=(item.get("title") or "").strip(),
        description=(item.get("description") or "").strip(),
        duration_seconds=int(item.get("duration") or 0),
        direct_urls=direct_urls,
        page_url=f"https://vk.com/video{owner_id}_{video_id}",
    )


CHANNEL_LOOKBACK = 1000
"""Сколько последних видео канала просматривать в поиске неопубликованного.

Было 20 — и этого хватило, чтобы Кино встало на трое суток при источнике на тысячу
фильмов (13.08.2026). Каждая неудачная попытка помечает видео взятым, окно в 20 записей
выгорает за пару недель, и дальше софт честно пишет «новых видео в источнике нет», хотя
на канале их сотни.

ТЗ владельца 2026-08-14: «мне надо чтобы все 1000 фильмов были» — окно равно всему
каналу. Цена приемлема ровно потому, что список запрашивается НЕ на каждом тике:
`should_run_daily_video` отсекает раньше, и до перечисления канала дело доходит только
когда фильм действительно пора публиковать (раз в сутки плюс повторы после сбоя).
Понадобится ещё глубже — поднимать здесь, но тогда стоит сначала посмотреть, не дешевле
ли кешировать список между попытками."""


def list_youtube_channel_videos(
    channel_url: str, *, count: int = CHANNEL_LOOKBACK
) -> list[dict[str, str]]:
    """Список видео канала (новые первыми, "плоское" извлечение — без скачивания и без
    полных метаданных каждого видео, быстрый один запрос). Возвращает
    [{"id", "title", "url"}, ...]."""
    import yt_dlp

    videos_url = channel_url.rstrip("/") + "/videos"
    options = ytdlp_options(extract_flat="in_playlist", playlistend=count)
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(videos_url, download=False)
    entries = info.get("entries") or [] if info else []
    return [
        {
            "id": entry["id"],
            "title": entry.get("title") or "",
            "url": entry.get("url") or f"https://www.youtube.com/watch?v={entry['id']}",
        }
        for entry in entries
        if entry and entry.get("id")
    ]


def fetch_youtube_video_details(video_id: str) -> SourceVideo:
    """Полные метаданные одного YouTube-видео (название/описание/длительность) без
    скачивания самого файла — нужны для AI-рерайта и решения "публиковать ли без AI"."""
    import yt_dlp

    url = f"https://www.youtube.com/watch?v={video_id}"
    options = ytdlp_options(skip_download=True)
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    return SourceVideo(
        ref=f"youtube_{video_id}",
        title=(info.get("title") or "").strip(),
        description=(info.get("description") or "").strip(),
        duration_seconds=int(info.get("duration") or 0),
        direct_urls={},
        page_url=url,
    )


def pick_unreposted_youtube(
    channel_url: str, reposted_refs: set[str], *, count: int = CHANNEL_LOOKBACK
) -> SourceVideo | None:
    """Самое свежее видео канала, которое ещё не публиковалось. Полные метаданные
    запрашиваются только для кандидата (не для всего списка — экономия запросов)."""
    for entry in list_youtube_channel_videos(channel_url, count=count):
        ref = f"youtube_{entry['id']}"
        if ref in reposted_refs:
            continue
        try:
            video = fetch_youtube_video_details(entry["id"])
        except Exception as error:
            logger.warning("YouTube-видео %s: не удалось получить метаданные: %s", ref, error)
            continue
        if video.duration_seconds <= 0:
            continue
        return video
    return None


def pick_unreposted(
    videos: list[SourceVideo], reposted_refs: set[str]
) -> SourceVideo | None:
    """Самое свежее видео, которое ещё не публиковалось (video.get отдаёт новые первыми).
    Видео без длительности (трансляции/битые) пропускаются."""
    for video in videos:
        if video.duration_seconds <= 0:
            continue
        if video.ref in reposted_refs:
            continue
        return video
    return None


def download_video(
    video: SourceVideo, dest_dir: Path, *, max_height: int = MAX_DOWNLOAD_HEIGHT
) -> Path:
    """Скачать видео в dest_dir: лучшая прямая mp4-ссылка ≤max_height из video.get,
    при её отсутствии/обрыве — фолбэк yt-dlp по странице видео."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{video.ref}.mp4"

    heights = sorted((h for h in video.direct_urls if h <= max_height), reverse=True)
    for height in heights:
        try:
            _download_url(video.direct_urls[height], dest)
            logger.info("Видео %s скачано прямой ссылкой (%dp)", video.ref, height)
            return dest
        except Exception as error:
            logger.warning("Видео %s: прямая ссылка %dp не сработала: %s", video.ref, height, error)

    return _download_with_ytdlp(video, dest, max_height=max_height)


def _download_url(url: str, dest: Path) -> None:
    with requests.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()
        with open(dest, "wb") as file:
            for chunk in response.iter_content(chunk_size=1 << 20):
                file.write(chunk)


def _download_with_ytdlp(video: SourceVideo, dest: Path, *, max_height: int) -> Path:
    try:
        import yt_dlp
    except ImportError as error:
        raise VideoDownloadError(
            f"Нет прямых ссылок на {video.ref}, а yt-dlp не установлен"
        ) from error

    options = ytdlp_options(
        format=f"best[height<={max_height}][ext=mp4]/best[height<={max_height}]/best",
        outtmpl=str(dest.with_suffix("")) + ".%(ext)s",
    )
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([video.page_url])
    except Exception as error:
        raise VideoDownloadError(f"yt-dlp не смог скачать {video.page_url}: {error}") from error

    downloaded = list(dest.parent.glob(f"{dest.stem}.*"))
    if not downloaded:
        raise VideoDownloadError(f"yt-dlp отработал, но файл {video.ref} не найден")
    logger.info("Видео %s скачано через yt-dlp", video.ref)
    return downloaded[0]
