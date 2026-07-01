"""Общий DTO для постов, полученных из любого источника (TG/VK)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class FetchedPost:
    external_id: str
    text: str
    post_type: str
    views: int
    published_at: datetime
    has_media: bool
