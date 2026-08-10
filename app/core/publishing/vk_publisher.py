"""Публикация в VK: двухшаговая загрузка фото (раздел 13.3-13.4 SPEC.md)."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
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


POSTPONED_PREFIX = "postponed: "
"""Маркер «пост отложен, а не сломался».

Отложенная публикация — НЕ сбой сети VK, и circuit breaker её считать не должен:
иначе череда отложек откроет цепь, а жёсткая пара VK↔TG тогда перестанет публиковать
и в Telegram — встанет весь канал. Ровно по этой причине уже существует префикс
`throttled:` для отказов rate_guard (см. `_record_vk_breaker`)."""


class MediaUploadUnavailable(Exception):
    """У поста есть медиа, но личного токена для загрузки нет (пул занят).

    Не ошибка публикации, а сигнал «отложить»: пост возвращается в очередь и выйдет
    целым, когда аккаунт освободится."""


@dataclass(frozen=True)
class VKPublishResult:
    success: bool
    post_id: int | None
    error: str | None
    error_code: int | None = None
    attachment: str | None = None
    """«videoOWNER_ID» загруженной видеозаписи. Заполняется публикацией БЕЗ записи на
    стене (`publish_video_only`) — там post_id нет, а идентификатор видео нужен и для
    логов, и чтобы при желании прикрепить его позже."""


class VKPublisher:
    # Дефолты класса — часть тестов создаёт экземпляр через __new__ (минуя __init__)
    # и не трогает лимитеры; без этих дефолтов такой экземпляр падал бы AttributeError.
    _bucket: TokenBucket | None = None
    _cooldown: TokenBucket | None = None
    _group_key: str | None = None
    _upload_key: str | None = None
    # Экземпляр, собранный через __new__, считается уже разрешённым: провайдера у него
    # нет, а _upload_api тест подставляет сам.
    _upload_resolved: bool = True
    _upload_token_provider: "Callable[[], str | None] | None" = None
    _upload_token: str | None = None
    _require_media: bool = False

    def __init__(
        self,
        group_token: str,
        *,
        upload_token: str | None = None,
        upload_token_provider: "Callable[[], str | None] | None" = None,
        require_media: bool = False,
        token_bucket: TokenBucket | None = None,
        cooldown_bucket: TokenBucket | None = None,
    ) -> None:
        self._api = vk_api.VkApi(token=group_token).get_api()
        # photos.*/video.save требуют user-контекст (group-токен получает ошибку 27 —
        # см. известные грабли в CLAUDE.md) — если задан отдельный upload_token (личный
        # аккаунт с правами админа группы), ТОЛЬКО загрузка медиа идёт через него, а
        # сам wall.post всегда через group_token (from_group=1) — минимизирует то, что
        # личный аккаунт вообще делает в автоматическом режиме.
        #
        # upload_token_provider — ЛЕНИВАЯ выдача токена: провайдер дёргается только когда
        # у поста реально есть медиа (см. _resolve_upload). Это принципиально при пуле
        # токенов: раньше токен занимался в момент СБОРКИ публикатора, то есть на каждый
        # рассмотренный пост — включая посты без медиа и те, что тут же отклонял
        # rate_guard. Аккаунт при этом блокировался зазором на 10 минут впустую, и пул из
        # двух аккаунтов голодал (2026-08-04: 2740 отказов против 43 выдач).
        self._require_media = require_media
        self._upload_token_provider = upload_token_provider
        self._upload_resolved = upload_token_provider is None
        self._upload_token: str | None = upload_token
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

    def _resolve_upload(self) -> None:
        """Забрать личный токен у провайдера — ровно один раз и только под реальную
        загрузку медиа. Провайдер вернул None (все аккаунты пула заняты) — остаёмся на
        групповом токене, а решение «публиковать ли текстом» принимает вызывающий по
        require_media."""
        if self._upload_resolved:
            return
        self._upload_resolved = True
        token = self._upload_token_provider() if self._upload_token_provider else None
        if not token:
            return
        self._upload_token = token
        self._upload_api = vk_api.VkApi(token=token).get_api()
        self._upload_key = token_key(token)

    def _has_upload_token(self) -> bool:
        return self._upload_token is not None

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
        try:
            attachments = self._build_attachments(
                group_id, image_paths, video_path,
                video_title=video_title, video_description=video_description,
            )
        except MediaUploadUnavailable as error:
            # Осознанно НЕ публикуем голый текст: пост с медиа, вышедший текстом, портит
            # ленту (жалоба владельца 2026-08-04). Неуспех возвращает пост в очередь —
            # он выйдет следующим циклом, когда аккаунт пула освободится.
            logger.warning("VK: публикация отложена — %s", error)
            return VKPublishResult(
                success=False, post_id=None, error=f"{POSTPONED_PREFIX}{error}", error_code=None
            )

        # Токен был, но VK всё равно отказал в загрузке (напр. [7] — у аккаунта нет права
        # добавлять видео в эту группу). Загрузка ловится best-effort внутри
        # _build_attachments, поэтому пустой список здесь — единственный признак, что
        # медиа не доехало. Инвариант тот же: пост с медиа не выходит текстом.
        if self._require_media and (image_paths or video_path is not None) and not attachments:
            logger.warning(
                "VK: публикация отложена — медиа не загрузилось, текстом не публикуем"
            )
            return VKPublishResult(
                success=False,
                post_id=None,
                error=f"{POSTPONED_PREFIX}медиа не загрузилось",
                error_code=None,
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

    def publish_video_only(
        self,
        *,
        group_id: int,
        video_path: Path,
        title: str | None = None,
        description: str | None = None,
        as_clip: bool = False,
    ) -> VKPublishResult:
        """Залить ролик в раздел сообщества, НЕ создавая запись на стене.

        ТЗ владельца 2026-08-10: «фильмы и клипы можешь не публиковать как посты, пусть
        фильмы идут в видео, а клипы в клипы». Стена остаётся под текстовые посты, а
        ролики живут в каталоге сообщества — там у них своё SEO-поле (название +
        описание), которое поиск индексирует отдельно от записи.

        as_clip=True — попытаться положить ролик в раздел «Клипы» (`shortVideo.create`).
        Метода нет в публичной схеме VK API, поэтому любая его осечка — не ошибка
        публикации: молча уходим на обычный `video.save`, где вертикальный короткий
        ролик всё равно попадает в клипы автоматически."""
        self._resolve_upload()
        if self._require_media and not self._has_upload_token():
            error = "личный токен для загрузки медиа недоступен (все аккаунты пула заняты)"
            logger.warning("VK: заливка ролика отложена — %s", error)
            return VKPublishResult(
                success=False, post_id=None, error=f"{POSTPONED_PREFIX}{error}"
            )

        if self._cooldown is not None:
            self._cooldown.wait(self._upload_key)

        try:
            if as_clip:
                attachment = self._upload_clip(group_id, video_path, description=description)
            else:
                attachment = self._upload_video(
                    group_id, video_path, title=title, description=description
                )
        except Exception as error:
            logger.error("VK: не удалось залить ролик %s: %s", video_path, error)
            return VKPublishResult(
                success=False,
                post_id=None,
                error=str(error),
                error_code=getattr(error, "code", None),
            )

        logger.info("VK: ролик %s залит в сообщество как %s", video_path.name, attachment)
        return VKPublishResult(success=True, post_id=None, error=None, attachment=attachment)

    def _upload_clip(
        self, group_id: int, video_path: Path, *, description: str | None = None
    ) -> str:
        """Раздел «Клипы» через `shortVideo.create`; не вышло — обычная видеозапись.

        `shortVideo.*` в публичной схеме VK (VKCOM/vk-api-schema) отсутствует — метод
        закрытый, его поведение может измениться без предупреждения. Поэтому здесь ровно
        один осторожный вызов и безусловный фолбэк: клип, уехавший в раздел «Видео»,
        для канала не потеря, а исключение наружу — потеря."""
        self._wait_upload()
        try:
            created = self._upload_api.shortVideo.create(
                group_id=abs(group_id),
                description=description or "",
                file_size=video_path.stat().st_size,
            )
            _post_video_file(created["upload_url"], video_path, field_name="file")
            return f"video{created['owner_id']}_{created['video_id']}"
        except Exception as error:
            logger.warning(
                "VK: раздел «Клипы» недоступен (%s) — гружу %s обычной видеозаписью",
                error, video_path.name,
            )
        return self._upload_video(
            group_id, video_path, title=video_path.stem, description=description
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

        # Медиа есть — только теперь занимаем аккаунт в пуле.
        self._resolve_upload()
        if self._require_media and not self._has_upload_token():
            raise MediaUploadUnavailable(
                "личный токен для загрузки медиа недоступен (все аккаунты пула заняты)"
            )

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


def _post_video_file(upload_url: str, video_path: Path, field_name: str = "video_file") -> None:
    """Загрузить файл видео ПОТОКОМ, не читая его целиком в память.

    Критично для VPS (2026-07-28): `requests.post(..., files={...})` строит multipart-тело
    в памяти целиком — фильм на 655 МБ при 961 МБ RAM мгновенно ронял процесс по OOM
    (`killed status=9`), причём ДО отметки «опубликовано» в БД, из-за чего следующий цикл
    качал тот же фильм заново — за ночь 7 перезаливок одного видео. MultipartEncoder
    отдаёт тело генератором, потребление памяти не зависит от размера файла.
    """
    with open(video_path, "rb") as file:
        encoder = MultipartEncoder(fields={field_name: (video_path.name, file, "video/mp4")})
        response = requests.post(
            upload_url,
            data=encoder,
            headers={"Content-Type": encoder.content_type},
            timeout=VIDEO_UPLOAD_TIMEOUT_SECONDS,
        )
    response.raise_for_status()
