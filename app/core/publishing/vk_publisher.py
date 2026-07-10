"""Публикация в VK: двухшаговая загрузка фото (раздел 13.3-13.4 SPEC.md)."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import requests
import vk_api

from app.core.publishing.vk_errors import VKErrorClass, classify_vk_error

logger = logging.getLogger("publishing")

RETRY_DELAYS_SECONDS = [0, 5, 30, 120]

# На этих классах ошибок ретраить нельзя — дальнейшие попытки только углубляют
# флуд-бан. Выходим сразу, отдаём код наверх, circuit breaker откроет паузу.
_FAIL_FAST_CLASSES = frozenset({VKErrorClass.RATE_LIMIT, VKErrorClass.AUTH_BLOCKED})


@dataclass(frozen=True)
class VKPublishResult:
    success: bool
    post_id: int | None
    error: str | None
    error_code: int | None = None


class VKPublisher:
    def __init__(self, group_token: str, *, upload_token: str | None = None) -> None:
        self._api = vk_api.VkApi(token=group_token).get_api()
        # photos.*/video.save требуют user-контекст (group-токен получает ошибку 27 —
        # см. известные грабли в CLAUDE.md) — если задан отдельный upload_token (личный
        # аккаунт с правами админа группы), ТОЛЬКО загрузка медиа идёт через него, а
        # сам wall.post всегда через group_token (from_group=1) — минимизирует то, что
        # личный аккаунт вообще делает в автоматическом режиме.
        self._upload_api = (
            vk_api.VkApi(token=upload_token).get_api() if upload_token else self._api
        )

    def publish(
        self,
        *,
        group_id: int,
        text: str,
        image_paths: list[Path] | None = None,
        video_path: Path | None = None,
    ) -> VKPublishResult:
        image_paths = image_paths or []
        # Загрузка медиа — best-effort: если фото/видео не залилось (напр. групповой
        # токен не имеет доступа к photos.getWallUploadServer — ошибка 27), публикуем
        # текст без него, а не роняем весь пост. Цель — пост всё равно уходит в VK.
        attachments = self._build_attachments(group_id, image_paths, video_path)
        # КРИТИЧНО (найдено 2026-07-05): photos.saveWallPhoto через личный upload_token
        # сохраняет фото за ЛИЧНЫМ owner_id (не за группой) — групповой _api не имеет
        # доступа к этому объекту (подтверждено вживую: photos.getById той же фотки той
        # же учёткой сразу после аплоада — "[200] Access denied"). wall.post групповым
        # токеном с таким attachment молча публикует пост БЕЗ вложения (VK не роняет
        # запрос ошибкой) — отсюда посты "без медиа" при формально успешной загрузке.
        # Постить с вложением нужно ТЕМ ЖЕ токеном, что и грузил фото; from_group=1
        # всё равно публикует от имени группы. Без вложений — как раньше, group_token
        # (минимизирует, что личный аккаунт делает в автоматическом режиме).
        poster_api = self._upload_api if attachments else self._api
        last_error: Exception | None = None

        for attempt, delay in enumerate(RETRY_DELAYS_SECONDS, start=1):
            if delay:
                time.sleep(delay)
            try:
                response = poster_api.wall.post(
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
                if classify_vk_error(error) in _FAIL_FAST_CLASSES:
                    break  # rate-limit/бан — не долбить, отдать код наверх для breaker

        logger.error("Публикация в VK не удалась после всех попыток: %s", last_error)
        return VKPublishResult(
            success=False,
            post_id=None,
            error=str(last_error),
            error_code=getattr(last_error, "code", None),
        )

    def _build_attachments(
        self, group_id: int, image_paths: list[Path], video_path: Path | None
    ) -> list[str]:
        # Видео есть — прикрепляем ТОЛЬКО его, фото игнорируем: так VK-пост
        # совпадает с TG-постом (TelegramPublisher при наличии видео шлёт только
        # видео). Один и тот же пост во всех соцсетях — явный запрос пользователя
        # 2026-07-05 («одинаковый пост во все соцсети», раньше VK давал фото+видео,
        # а TG только видео).
        if video_path is not None:
            try:
                return [self._upload_video(group_id, video_path)]
            except Exception as error:
                logger.warning("VK: не удалось загрузить видео %s, публикую без него: %s", video_path, error)
                return []

        attachments: list[str] = []
        for path in image_paths:
            try:
                attachments.append(self._upload_photo(group_id, path))
            except Exception as error:
                logger.warning("VK: не удалось загрузить фото %s, публикую без него: %s", path, error)
        return attachments

    def _upload_photo(self, group_id: int, image_path: Path) -> str:
        upload_server = self._upload_api.photos.getWallUploadServer(group_id=abs(group_id))
        with open(image_path, "rb") as file:
            upload_result = self._upload_to_server(upload_server["upload_url"], file)

        saved = self._upload_api.photos.saveWallPhoto(
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

    def _upload_video(self, group_id: int, video_path: Path) -> str:
        """video.save отдаёт upload_url + готовые video_id/owner_id сразу (в отличие от
        фото, где saveWallPhoto — отдельный шаг ПОСЛЕ загрузки файла) — сама загрузка
        файла на upload_url лишь финализирует уже созданную запись."""
        # wallpost=0, НЕ Python bool False: vk_api сериализует False в строку "False",
        # которую VK не может распарсить как флаг и отвечает generic [10] Internal
        # server error (подтверждено вживую 2026-07-04) — выглядело как архитектурный
        # запрет видео через групповой upload-токен, а на деле баг сериализации параметра.
        save_result = self._upload_api.video.save(
            name=video_path.stem, group_id=abs(group_id), wallpost=0
        )
        with open(video_path, "rb") as file:
            response = requests.post(
                save_result["upload_url"], files={"video_file": file}, timeout=300
            )
        response.raise_for_status()
        return f"video{save_result['owner_id']}_{save_result['video_id']}"
