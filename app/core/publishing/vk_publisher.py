"""Публикация в VK: двухшаговая загрузка фото (раздел 13.3-13.4 SPEC.md)."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import requests
import vk_api

logger = logging.getLogger("publishing")

RETRY_DELAYS_SECONDS = [0, 5, 30, 120]


@dataclass(frozen=True)
class VKPublishResult:
    success: bool
    post_id: int | None
    error: str | None


class VKPublisher:
    def __init__(self, group_token: str) -> None:
        session = vk_api.VkApi(token=group_token)
        self._api = session.get_api()

    def publish(
        self, *, group_id: int, text: str, image_paths: list[Path] | None = None
    ) -> VKPublishResult:
        image_paths = image_paths or []
        last_error: Exception | None = None

        for attempt, delay in enumerate(RETRY_DELAYS_SECONDS, start=1):
            if delay:
                time.sleep(delay)
            try:
                attachments = [self._upload_photo(group_id, path) for path in image_paths]
                response = self._api.wall.post(
                    owner_id=-abs(group_id),
                    message=text,
                    attachments=",".join(attachments) if attachments else None,
                    from_group=1,
                )
                return VKPublishResult(success=True, post_id=response["post_id"], error=None)
            except Exception as error:  # vk_api поднимает разные ApiError-подклассы
                last_error = error
                logger.warning(
                    "Публикация в VK не удалась (попытка %d/%d): %s",
                    attempt,
                    len(RETRY_DELAYS_SECONDS),
                    error,
                )

        logger.error("Публикация в VK не удалась после всех попыток: %s", last_error)
        return VKPublishResult(success=False, post_id=None, error=str(last_error))

    def _upload_photo(self, group_id: int, image_path: Path) -> str:
        upload_server = self._api.photos.getWallUploadServer(group_id=abs(group_id))
        with open(image_path, "rb") as file:
            upload_result = self._upload_to_server(upload_server["upload_url"], file)

        saved = self._api.photos.saveWallPhoto(
            group_id=abs(group_id),
            photo=upload_result["photo"],
            server=upload_result["server"],
            hash=upload_result["hash"],
        )
        photo = saved[0]
        return f"photo{photo['owner_id']}_{photo['id']}"

    def _upload_to_server(self, upload_url: str, file) -> dict:
        response = requests.post(upload_url, files={"photo": file}, timeout=60)
        response.raise_for_status()
        return response.json()
