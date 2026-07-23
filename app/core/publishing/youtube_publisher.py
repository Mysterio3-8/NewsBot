"""Загрузка видео на свой YouTube-канал через Data API v3 (ТЗ 2026-07-22).

Публикуем и фильмы, и клипы (клипы вертикальные ≤60с → YouTube сам относит их к Shorts,
плюс #Shorts в заголовке для надёжности). OAuth-refresh-токен из .env, библиотека сама
обновляет access-токен. Best-effort: сбой загрузки не рушит остальные сети — как VK/TG.

Квота Data API — 10 000 единиц/сутки, одна загрузка = 1600 → максимум ~6 роликов в день
(2 фильма + 4 клипа впритык). Больше — часть уедет только на следующие сутки.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("publishing")

TOKEN_URI = "https://oauth2.googleapis.com/token"
UPLOAD_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
# Категория 24 = Entertainment (у YouTube фиксированный справочник категорий).
DEFAULT_CATEGORY_ID = "24"


@dataclass(frozen=True)
class YouTubeCredentials:
    client_id: str
    client_secret: str
    refresh_token: str


class YouTubePublisher:
    def __init__(self, credentials: YouTubeCredentials, *, privacy: str = "public") -> None:
        self._credentials = credentials
        self._privacy = privacy

    def upload(
        self, video_path: Path, *, title: str, description: str, is_short: bool
    ) -> str | None:
        """Вернуть video_id при успехе, иначе None (best-effort, ошибку не поднимаем).
        Тяжёлые импорты google-клиента внутри метода: без загрузки на YouTube они не
        нужны, и модуль остаётся импортируемым даже без установленной библиотеки."""
        try:
            youtube = self._build_client()
            request_body = {
                "snippet": {
                    "title": title,
                    "description": description,
                    "categoryId": DEFAULT_CATEGORY_ID,
                },
                "status": {"privacyStatus": self._privacy, "selfDeclaredMadeForKids": False},
            }
            media = self._build_media(video_path)
            response = (
                youtube.videos()
                .insert(part="snippet,status", body=request_body, media_body=media)
                .execute()
            )
            video_id = response.get("id")
            logger.info(
                "YouTube: загружено %s (id=%s, %s)",
                video_path.name, video_id, "Shorts" if is_short else "видео",
            )
            return video_id
        except Exception:
            logger.exception("YouTube: загрузка %s не удалась", video_path.name)
            return None

    def _build_client(self):
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials(
            token=None,
            refresh_token=self._credentials.refresh_token,
            client_id=self._credentials.client_id,
            client_secret=self._credentials.client_secret,
            token_uri=TOKEN_URI,
            scopes=UPLOAD_SCOPES,
        )
        return build("youtube", "v3", credentials=creds, cache_discovery=False)

    @staticmethod
    def _build_media(video_path: Path):
        from googleapiclient.http import MediaFileUpload

        # resumable=True: докачивает большой файл (фильм — сотни МБ) при обрыве связи.
        return MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/*")
