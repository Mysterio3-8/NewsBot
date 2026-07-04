"""Общий DTO для постов, полученных из любого источника (TG/VK)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class FetchedPost:
    external_id: str
    text: str
    post_type: str
    views: int
    published_at: datetime
    has_media: bool
    media_urls: list[str] = field(default_factory=list)
    video_path: str | None = None  # локальный путь к скачанному видео (см. video-pipeline)
