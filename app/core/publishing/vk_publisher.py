"""Публикация в VK: двухшаговая загрузка фото (раздел 13.3-13.4 SPEC.md)."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import requests
import vk_api
from requests_toolbelt.multipart.encoder import MultipartEncoder

from app.core.publishing.token_bucket import TokenBucket, token_key
from app.core.publishing.vk_errors import VKErrorClass, classify_vk_error

logger = logging.getLogger("publishing")

RETRY_DELAYS_SECONDS = [0, 5, 30, 120]

# Фильм в сотни мегабайт заливается долго — таймаут на весь поток, а не на первый байт.
VIDEO_UPLOAD_TIMEOUT_SECONDS = 1800

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
    # Дефолты класса — часть тестов создаёт экземпляр через __new__ (минуя __init__)
    # и не трогает лимитеры; без этих дефолтов такой экземпляр падал бы AttributeError.
    _bucket: TokenBucket | None = None
    _cooldown: TokenBucket | None = None
    _group_key: str | None = None
    _upload_key: str | None = None

    def __init__(
        self,
        group_token: str,
        *,
        upload_token: str | None = None,
        token_bucket: TokenBucket | None = None,
        cooldown_bucket: TokenBucket | None = None,
    ) -> None:
        self._api = vk_api.VkApi(token=group_token).get_api()
        # photos.*/video.save требуют user-контекст (group-токен получает ошибку 27 —
        # см. известные грабли в CLAUDE.md) — если задан отдельный upload_token (личный
        # аккаунт с правами админа группы), ТОЛЬКО загрузка медиа идёт через него, а
        # сам wall.post всегда через group_token (from_group=1) — минимизирует то, что
        # личный аккаунт вообще делает в автоматическом режиме.
        self._upload_api = (
            vk_api.VkApi(token=upload_token).get_api() if upload_token else self._api
        )
        # token_bucket — технический burst-лимитер (не более 2 запр/сек на токен),
        # нужен ВНУТРИ одной загрузки (getWallUploadServer→saveWallPhoto — иначе рвётся
        # upload_url). cooldown_bucket — ЖЁСТКИЙ лимит НА ОПЕРАЦИЮ личного токена (ТЗ
        # пользователя 2026-07-10: "даже если публикация будет идти через 10 минут,
        # главное бана избежать") — ждём ОДИН раз перед началом загрузки медиа поста
        # целиком, не между сырыми вызовами внутри неё (см. _build_attachments).
        # Групповой и личный токен пейсятся НЕЗАВИСИМО (разные ключи) — если это один
        # физический токен (upload_token не задан), ключи совпадают, что и нужно.
        self._bucket = token_bucket
        self._cooldown = cooldown_bucket
        self._group_key = token_key(group_token)
        self._upload_key = token_key(upload_token) if upload_token else self._group_key

    def publish(
        self,
        *,
        group_id: int,
        text: str,
        image_paths: list[Path] | None = None,
        video_path: Path | None = None,
        video_title: str | None = None,
        video_description: str | None = None,
    ) -> VKPublishResult:
        image_paths = image_paths or []
        # Загрузка медиа — best-effort: если фото/видео не залилось (напр. групповой
        # токен не имеет доступа к photos.getWallUploadServer — ошибка 27), публикуем
        # текст без него, а не роняем весь пост. Цель — пост всё равно уходит в VK.
        attachments = self._build_attachments(
            group_id, image_paths, video_path,
            video_title=video_title, video_description=video_description,
        )
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
        poster_key = self._upload_key if attachments else self._group_key
        last_error: Exception | None = None

        for attempt, delay in enumerate(RETRY_DELAYS_SECONDS, start=1):
            if delay:
                time.sleep(delay)
            if self._bucket is not None:
                self._bucket.wait(poster_key)
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
        self,
        group_id: int,
        image_paths: list[Path],
        video_path: Path | None,
        *,
        video_title: str | None = None,
        video_description: str | None = None,
    ) -> list[str]:
        if not image_paths and video_path is None:
            return []  # нет медиа — личный токен вообще не трогаем, ждать нечего

        # ЖЁСТКИЙ кулдаун — ОДИН раз перед началом загрузки МЕДИА ЭТОГО ПОСТА целиком
        # (не между getWallUploadServer/saveWallPhoto — там нужна скорость, иначе рвётся
        # upload_url). Именно здесь личный токен впервые трогается для этого поста.
        if self._cooldown is not None:
            self._cooldown.wait(self._upload_key)

        # Видео есть — прикрепляем ТОЛЬКО его, фото игнорируем: так VK-пост
        # совпадает с TG-постом (TelegramPublisher при наличии видео шлёт только
        # видео). Один и тот же пост во всех соцсетях — явный запрос пользователя
        # 2026-07-05 («одинаковый пост во все соцсети», раньше VK давал фото+видео,
        # а TG только видео).
        if video_path is not None:
            try:
                return [
                    self._upload_video(
                        group_id, video_path, title=video_title, description=video_description
                    )
                ]
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

    def _wait_upload(self) -> None:
        if self._bucket is not None:
            self._bucket.wait(self._upload_key)

    def _upload_photo(self, group_id: int, image_path: Path) -> str:
        # Личный upload-токен грузит фото — двумя отдельными VK API-вызовами, каждый
        # пейсится независимо (лимит VK — на запрос, не на операцию).
        self._wait_upload()
        upload_server = self._upload_api.photos.getWallUploadServer(group_id=abs(group_id))
        with open(image_path, "rb") as file:
            upload_result = self._upload_to_server(upload_server["upload_url"], file)

        self._wait_upload()
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

    def _upload_video(
        self,
        group_id: int,
        video_path: Path,
        *,
        title: str | None = None,
        description: str | None = None,
    ) -> str:
        """video.save отдаёт upload_url + готовые video_id/owner_id сразу (в отличие от
        фото, где saveWallPhoto — отдельный шаг ПОСЛЕ загрузки файла) — сама загрузка
        файла на upload_url лишь финализирует уже созданную запись. title/description —
        название и описание видеозаписи в разделе «Видео» группы (ежедневный видео-репост
        публикует видео с человеческим названием, не именем файла)."""
        # wallpost=0, НЕ Python bool False: vk_api сериализует False в строку "False",
        # которую VK не может распарсить как флаг и отвечает generic [10] Internal
        # server error (подтверждено вживую 2026-07-04) — выглядело как архитектурный
        # запрет видео через групповой upload-токен, а на деле баг сериализации параметра.
        self._wait_upload()
        save_params: dict = {
            "name": title or video_path.stem,
            "group_id": abs(group_id),
            "wallpost": 0,
        }
        if description:
            save_params["description"] = description
        save_result = self._upload_api.video.save(**save_params)
        _post_video_file(save_result["upload_url"], video_path)
        return f"video{save_result['owner_id']}_{save_result['video_id']}"


def _post_video_file(upload_url: str, video_path: Path) -> None:
    """Загрузить файл видео ПОТОКОМ, не читая его целиком в память.

    Критично для VPS (2026-07-28): `requests.post(..., files={...})` строит multipart-тело
    в памяти целиком — фильм на 655 МБ при 961 МБ RAM мгновенно ронял процесс по OOM
    (`killed status=9`), причём ДО отметки «опубликовано» в БД, из-за чего следующий цикл
    качал тот же фильм заново — за ночь 7 перезаливок одного видео. MultipartEncoder
    отдаёт тело генератором, потребление памяти не зависит от размера файла.
    """
    with open(video_path, "rb") as file:
        encoder = MultipartEncoder(fields={"video_file": (video_path.name, file, "video/mp4")})
        response = requests.post(
            upload_url,
            data=encoder,
            headers={"Content-Type": encoder.content_type},
            timeout=VIDEO_UPLOAD_TIMEOUT_SECONDS,
        )
    response.raise_for_status()
