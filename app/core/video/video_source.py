"""Выбор и скачивание видео из VK-группы-источника для ежедневного видео-репоста."""
from __future__ import annotations

import logging
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


@dataclass(frozen=True)
class SourceVideo:
    ref: str  # "ownerid_videoid" — ключ дедупа в reposted_videos
    owner_id: int
    video_id: int
    title: str
    description: str
    duration_seconds: int
    direct_urls: dict[int, str]  # высота (480/720...) → прямая mp4-ссылка из video.get
    page_url: str  # страница видео — вход для yt-dlp, если прямых ссылок нет


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

    options = {
        "format": f"best[height<={max_height}][ext=mp4]/best[height<={max_height}]/best",
        "outtmpl": str(dest.with_suffix("")) + ".%(ext)s",
        "quiet": True,
        "no_warnings": True,
    }
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
