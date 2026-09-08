"""Чтение постов VK-сообществ через vk_api (раздел 7 SPEC.md)."""
from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
import vk_api

from app.core.monitoring.models import FetchedPost
from app.core.publishing.token_bucket import TokenBucket, token_key
from app.paths import OUTPUT_DIR

logger = logging.getLogger("monitoring")

# VK отдаёт прямые HTTP-URL на CDN (в отличие от Telegram) — но весь остальной
# пайплайн (detect_foreign_watermark, Watermarker, VKPublisher._upload_photo)
# ожидает ЛОКАЛЬНЫЙ путь файла и делает Image.open()/open(path, "rb") — на
# сырой URL это тихо падает (ловится try/except выше по стеку) и пост уходит
# без фото. Скачиваем сюда, как TelegramFetcher._download_photo (см. там же).
VK_MEDIA_DIR = OUTPUT_DIR / "vk_raw_media"


def vk_post_to_fetched_post(item: dict[str, Any]) -> FetchedPost:
    """Преобразование ответа wall.get в FetchedPost. Чистая функция — тестируется без сети."""
    return FetchedPost(
        external_id=str(item["id"]),
        text=item.get("text", ""),
        post_type=_classify_vk_post_type(item),
        views=item.get("views", {}).get("count", 0),
        published_at=datetime.fromtimestamp(item["date"], tz=timezone.utc),
        has_media=bool(item.get("attachments")),
        media_urls=_extract_photo_urls(item),
    )


def _extract_photo_urls(item: dict[str, Any]) -> list[str]:
    urls = []
    for attachment in item.get("attachments", []):
        if attachment.get("type") != "photo":
            continue
        sizes = attachment.get("photo", {}).get("sizes", [])
        if not sizes:
            continue
        largest = max(sizes, key=lambda s: s.get("width", 0) * s.get("height", 0))
        urls.append(largest["url"])
    return urls


def _classify_vk_post_type(item: dict[str, Any]) -> str:
    if item.get("marked_as_ads"):
        return "ad"
    if item.get("is_pinned"):
        return "pinned"
    attachments = item.get("attachments", [])
    if any(a.get("type") == "poll" for a in attachments):
        return "poll"
    return "text"


@dataclasses.dataclass(frozen=True)
class WallPage:
    """Страница стены: годные посты и сколько записей всего просмотрено на ней."""

    posts: list[FetchedPost]
    items_seen: int


class VKFetcher:
    # Дефолты класса — тесты создают экземпляр через __new__ (минуя __init__) и не
    # трогают лимитеры; без этих дефолтов такой экземпляр падал бы AttributeError.
    _bucket: TokenBucket | None = None
    _bucket_key: str | None = None
    _cooldown: TokenBucket | None = None

    def __init__(
        self,
        user_token: str,
        *,
        fallback_tokens: tuple[str, ...] | list[str] = (),
        token_bucket: TokenBucket | None = None,
        cooldown_bucket: TokenBucket | None = None,
    ) -> None:
        """token_bucket — технический burst-лимитер (не более 2 запр/сек на токен).
        cooldown_bucket — ЖЁСТКИЙ лимит на ОПЕРАЦИИ личного токена (ТЗ пользователя
        2026-07-10: "даже если публикация будет идти через 10 минут, главное бана
        избежать") — личный VK-токен (VK_USER_TOKEN) используется ТОЛЬКО для чтения
        источников, минимизируем темп его вызовов обоими лимитерами."""
        # Токенов может быть несколько: первым идёт официальный (своё приложение VK ID),
        # запасным — прежний. Так переход не ломает прод: пока официальному не выдали
        # расширенные доступы или он отозван, чтение продолжается старым токеном.
        # Живой случай 2026-09-08: VK_USER_TOKEN отозвали посреди суток, и источники
        # Кино перестали читаться совсем — с запасным этого бы не случилось.
        self._tokens = [token for token in (user_token, *fallback_tokens) if token]
        self._token_index = 0
        self._api = vk_api.VkApi(token=self._tokens[0]).get_api()
        self._bucket = token_bucket
        self._bucket_key = token_key(self._tokens[0])
        self._cooldown = cooldown_bucket

    def _switch_token(self) -> bool:
        """Перейти на следующий токен. False — запасных не осталось."""
        if self._token_index + 1 >= len(self._tokens):
            return False
        self._token_index += 1
        token = self._tokens[self._token_index]
        self._api = vk_api.VkApi(token=token).get_api()
        # Ключ лимитера привязан к токену: у нового аккаунта свой счёт запросов, и
        # чужой израсходованный лимит не должен его тормозить.
        self._bucket_key = token_key(token)
        logger.warning("VK: токен отозван, перехожу на запасной (%d из %d)",
                       self._token_index + 1, len(self._tokens))
        return True

    def _call(self, method: str, **kwargs):
        """Вызов метода VK с переходом на запасной токен при «[5] authorization failed».

        Повторяем ТОЛЬКО на коде 5: остальные ошибки (нет прав, приватная стена, лимит)
        на другом токене дадут ровно то же, и перебор лишь удвоил бы нагрузку на
        аккаунты — ту самую, из-за которой их банят."""
        while True:
            call = self._api
            for part in method.split("."):
                call = getattr(call, part)
            try:
                return call(**kwargs)
            except vk_api.exceptions.ApiError as error:
                if error.code != 5 or not self._switch_token():
                    raise

    def fetch_engagement(self, group_id: int, vk_post_ids: list[int]) -> dict[int, int]:
        """Просмотры+лайки постов группы (для еженедельного репоста лучшего). Личный
        VK_USER_TOKEN — wall.getById групповым токеном недоступен (VK [27]). Возвращает
        {vk_post_id: просмотры + лайки}; недоступные посты пропускаются."""
        if not vk_post_ids:
            return {}
        owner = -abs(group_id)
        refs = ",".join(f"{owner}_{pid}" for pid in vk_post_ids)
        try:
            items = self._call("wall.getById", posts=refs)
        except Exception as error:
            logger.warning("VK wall.getById не удался: %s", error)
            return {}
        scores: dict[int, int] = {}
        for item in items or []:
            views = (item.get("views") or {}).get("count", 0)
            likes = (item.get("likes") or {}).get("count", 0)
            scores[item["id"]] = int(views) + int(likes)
        return scores

    def fetch_group_videos(self, group_id: int, *, count: int = 100) -> list[dict[str, Any]]:
        """Видеозаписи группы (video.get, новые первыми) — для ежедневного видео-репоста.
        Личный VK_USER_TOKEN: групповым токеном чужая группа недоступна. Возвращает сырые
        item'ы VK (id, owner_id, title, description, duration, files...)."""
        if self._cooldown is not None:
            self._cooldown.wait(self._bucket_key)
        if self._bucket is not None:
            self._bucket.wait(self._bucket_key)
        response = self._call("video.get", owner_id=-abs(group_id), count=count)
        return response.get("items", [])

    def fetch_wall_page(
        self,
        group_id: int,
        *,
        offset: int,
        count: int,
        known_external_ids: set[str] | None = None,
        is_known=None,
    ) -> "WallPage":
        """Страница стены с произвольным смещением, БЕЗ ограничения по возрасту — обход
        источника вглубь, когда свежие посты кончились (ТЗ владельца 2026-08-30: «можно и
        старые, можно максимально даже вниз спускаться»).

        Возвращает и годные посты, и число просмотренных записей: пустой список постов
        сам по себе неоднозначен (все уже известны ИЛИ стена кончилась), а вызывающему
        нужно отличать одно от другого, чтобы вовремя пойти на новый круг.

        is_known(external_id) — ТОЧНАЯ проверка «этот пост уже обрабатывали», по БД.
        Список known_external_ids для обхода вглубь не годится: он обрезан последней
        тысячей записей, а на втором круге по большой стене всё, что старше этого окна,
        считалось бы новым — и фото качались бы заново на каждом круге, чтобы тут же
        быть отброшенными дедупом уже в пайплайне."""
        if self._cooldown is not None:
            self._cooldown.wait(self._bucket_key)
        if self._bucket is not None:
            self._bucket.wait(self._bucket_key)
        response = self._call("wall.get", owner_id=-abs(group_id), count=count, offset=offset)

        items = response.get("items") or []
        known_ids = known_external_ids or set()
        posts: list[FetchedPost] = []
        for item in items:
            post = vk_post_to_fetched_post(item)
            # Обе проверки идут ДО скачивания: media стоит трафика и места на диске,
            # а известный пост всё равно будет отброшен дальше по пайплайну.
            if post.external_id in known_ids:
                continue
            if is_known is not None and is_known(post.external_id):
                continue
            local_paths = self._download_photos(post.external_id, post.media_urls)
            posts.append(dataclasses.replace(post, media_urls=local_paths))
        return WallPage(posts=posts, items_seen=len(items))

    def fetch_recent_posts(
        self,
        group_id: int,
        *,
        max_age_hours: float,
        count: int = 50,
        known_external_ids: set[str] | None = None,
    ) -> list[FetchedPost]:
        """known_external_ids — последние обработанные ID этого источника (см.
        Repository.get_recent_external_ids). Без этого фильтра фото уже известных
        постов скачивались бы заново на КАЖДОМ цикле проверки, пока пост не выпадет
        из окна max_age_hours — тот же баг, что забивал диск на 100% для TelegramFetcher
        (см. известные грабли в CLAUDE.md), пока там не добавили тот же фильтр."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        known_ids = known_external_ids or set()
        posts: list[FetchedPost] = []

        if self._cooldown is not None:
            self._cooldown.wait(self._bucket_key)
        if self._bucket is not None:
            self._bucket.wait(self._bucket_key)
        response = self._call("wall.get", owner_id=-abs(group_id), count=count)
        for item in response["items"]:
            post = vk_post_to_fetched_post(item)
            if post.published_at < cutoff:
                continue
            if post.external_id in known_ids:
                continue
            local_paths = self._download_photos(post.external_id, post.media_urls)
            posts.append(dataclasses.replace(post, media_urls=local_paths))

        return posts

    def _download_photos(self, external_id: str, urls: list[str]) -> list[str]:
        if not urls:
            return []
        VK_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        local_paths: list[str] = []
        for index, url in enumerate(urls):
            dest = VK_MEDIA_DIR / f"{external_id}_{index}.jpg"
            try:
                response = requests.get(url, timeout=30)
                response.raise_for_status()
                dest.write_bytes(response.content)
            except Exception as error:
                logger.warning("VK: не удалось скачать фото поста %s: %s", external_id, error)
                continue
            local_paths.append(str(dest))
        return local_paths
