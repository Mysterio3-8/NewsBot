"""Ежедневный видео-репост: одно видео с YouTube-канала (или VK-группы) → наш канал + клипы.

Раз в день (для каналов с daily_video_youtube_channels и/или daily_video_group): берём
самое свежее ещё не публиковавшееся видео источника, скачиваем, AI переписывает название
и описание (если они есть), публикуем в наш канал видеозаписью + постом с видео, режем
файл на N вертикальных клипов и планируем их публикацию в случайное время по остатку
дня, после чего скачанный файл удаляется с диска. Публикация клипов — отдельным
частым джобом по плану из БД (переживает рестарт сервиса).

Источник по умолчанию — YouTube (запрос пользователя 2026-07-18): VK жёстко троттлит
видео-CDN для датацентр-IP VPS (скорость падает до единиц КБ/с при обычном канале VPS
200+ МБ/с) — полная докачка растягивалась бы на многие часы. VK-группа оставлена как
резервный путь (код рабочий), проверяется только если YouTube-каналы не настроены."""
from __future__ import annotations

import datetime
import logging
import os
import random
import re
from pathlib import Path

from app.core.channel_settings import ChannelSettings
from app.core.llm.clip_hook import generate_clip_hooks
from app.core.llm.client import LLMClient
from app.core.llm.video_rewriter import rewrite_video_texts
from app.core.monitoring.vk_fetcher import VKFetcher
from app.core.publishing.footer import FooterLinks, build_markdown_footer
from app.core.publishing.telethon_video_publisher import TelethonVideoPublisher
from app.core.publishing.vk_publisher import VKPublisher, VKPublishResult
from app.core.publishing.vk_queue_service import _build_vk_publish_text
from app.core.publishing.vk_token_pool import DEFAULT_DAILY_CAP, VkTokenPool
from app.core.publishing.youtube_description import (
    build_vk_group_url,
    build_youtube_description,
    build_youtube_title,
)
from app.core.maintenance.alerts import (
    LAST_FILM_ALERT_KEY,
    alert_once,
    build_video_failure_alert,
)
from app.core.maintenance.cleanup import (
    FILM_REQUIRED_FREE_MB,
    disk_status,
    emergency_cleanup,
    has_free_space,
)
from app.core.publishing.youtube_publisher import YouTubePublisher
from app.core.seo.builder import build_video_seo_description
from app.core.video.clip_cutter import cut_clips
from app.core.video.proxy_rotation import pick_working_proxy
from app.core.video.film_prep import prepare_film
from app.core.video.watermark import probe_dimensions
from app.core.video.video_source import (
    SourceVideo,
    download_video,
    pick_unreposted,
    pick_unreposted_youtube,
    source_video_from_item,
)
from app.db.models import Channel
from app.db.repository import Repository
from app.paths import OUTPUT_DIR, PROJECT_ROOT

logger = logging.getLogger("publishing")

DAILY_VIDEO_DIR = OUTPUT_DIR / "daily_video"
CLIPS_DIR = OUTPUT_DIR / "clips"
# ТЗ 2026-07-21: постим круглосуточно, пауза между клипами случайная 1.5-4 часа
# (раньше клипы жались в окно до 23:00 МСК с фиксированными 45 минутами).
CLIP_MIN_SPACING_MINUTES = 990
CLIP_MAX_SPACING_MINUTES = 1170
"""Пауза между клипами одного фильма — 16.5–19.5 часов.

Такой разброс задаёт ПОРЯДОК ДНЯ, который просил владелец 2026-08-06: «кино так фильм,
клип, пост, пост, пост, клип». Раскладка при одном фильме и двух клипах:

    фильм (T0) → клип 1 (T0+0.5..1.5ч) → пост → пост → пост → клип 2 (T0+17..21ч)

Первый клип идёт сразу за фильмом (стартовая пауза 30–90 мин), три текстовых поста
разъезжаются своим интервалом 330–460 мин, а второй клип закрывает сутки. Работает это
только потому, что публикация фильма и клипов теперь двигает тот же интервальный гейт,
что и посты (`repository.get_last_published_at`) — иначе пост мог выйти сразу за фильмом.

⚠️ Значения рассчитаны на 2 клипа. Поставите больше — пересчитайте: при трёх клипах
пауза в 18 часов вынесет последний за пределы суток."""
# Клип, который не удалось опубликовать сутки, считаем протухшим — не долбим VK вечно.
CLIP_EXPIRE_HOURS = 24

VIDEO_ALERT_COOLDOWN_HOURS = 24
"""Пауза между тревогами о фильме. Сутки, а не шесть часов: фильм ровно один в день,
и вторая тревога за те же сутки говорила бы о той же поломке (ТЗ 2026-08-14 «много
тревог не надо»)."""


def plan_clip_times(
    now: datetime.datetime,
    count: int,
    *,
    min_spacing_minutes: int = CLIP_MIN_SPACING_MINUTES,
    max_spacing_minutes: int = CLIP_MAX_SPACING_MINUTES,
    rng: random.Random | None = None,
) -> list[datetime.datetime]:
    """Моменты публикации клипов: первый через 30-90 минут, дальше каждый через
    случайную паузу min..max. Окна суток нет — постим круглосуточно (ТЗ 2026-07-21)."""
    rng = rng or random.Random()
    times: list[datetime.datetime] = []
    moment = now + datetime.timedelta(minutes=rng.uniform(30, 90))
    for _ in range(count):
        times.append(moment)
        moment += datetime.timedelta(minutes=rng.uniform(min_spacing_minutes, max_spacing_minutes))
    return times


def ensure_working_proxy(repo: Repository) -> None:
    """Перед тяжёлой работой убедиться, что выход прокси проходит барьер YouTube.

    Ставит найденный адрес в `YT_PROXY` — дальше его подхватывает `ytdlp_options`, и
    остальной код о ротации не знает вовсе. Проверка стоит один дешёвый запрос в сутки
    (джоб фильма и так ходит раз в день), а спасает от простоя, который иначе чинился бы
    руками через сутки после того, как владелец увидит пустую стену."""
    try:
        proxy = pick_working_proxy(repo)
    except Exception:  # noqa: BLE001 — ротация не должна ронять день
        logger.exception("Подбор прокси упал — идём с текущими настройками")
        return
    if proxy:
        os.environ["YT_PROXY"] = proxy


MAX_FILM_CANDIDATES = 3
"""Сколько роликов пробуем скачать за один заход, прежде чем сдаться.

Отказ бывает привязан к КОНКРЕТНОМУ ролику, а не к сети: 15.08 `bS3MsEksGNE` отдавал
403 на данных и с progressive-форматом, и с раздельными дорожками, тогда как соседние
ролики того же канала качались нормально. Раньше такой ролик стоил суток без кино —
теперь берём следующий.

Три попытки, а не «пока не получится»: каждая — это запрос метаданных и попытка
скачивания, а фильм и так один в сутки. Больше трёх подряд означает, что сломан не
ролик, а канал или выход, и перебор только добьёт IP."""


def _download_first_available(
    repo: Repository,
    channel: Channel,
    settings: ChannelSettings,
    vk_fetcher: VKFetcher | None,
    video: SourceVideo,
    llm_client: LLMClient,
) -> tuple[SourceVideo, str, str, Path] | None:
    """Скачать первый ролик, который поддастся. None — не поддался ни один.

    Отметка в БД ставится ДО скачивания (защита от бесконечной перекачки после OOM:
    2026-07-27 один фильм перезаливался семь раз за ночь), поэтому неудачный ролик
    автоматически выпадает из выбора на следующей итерации."""
    last_error: Exception | None = None
    for attempt in range(MAX_FILM_CANDIDATES):
        if video is None:
            break
        title, description = rewrite_video_texts(
            llm_client, title=video.title, description=video.description
        )
        repo.add_reposted_video(channel_id=channel.id, video_ref=video.ref, title=title or None)
        try:
            local_file = _prepare_local_file(_download_with_retry(repo, video), settings)
            return video, title, description, local_file
        except Exception as error:  # noqa: BLE001 — граница сети/диска
            last_error = error
            logger.warning(
                "Видео-репост [%s]: %s не скачался (%d/%d): %s",
                channel.name, video.ref, attempt + 1, MAX_FILM_CANDIDATES, str(error)[:90],
            )
            # Тревоги подбора здесь выключены: о неудаче скажет ОДНО сообщение ниже.
            # Иначе на один провал приходило два письма — «источники исчерпаны» и
            # «не скачался ни один», а владелец просил тревог поменьше.
            video = _pick_video(repo, channel, settings, vk_fetcher, alerts_enabled=False)

    logger.error("Видео-репост [%s]: ни один ролик не скачался", channel.name)
    _alert_video_failure(
        repo, channel.name, "фильм",
        f"не скачался ни один из {MAX_FILM_CANDIDATES} роликов — {last_error}",
    )
    return None


def _download_with_retry(repo: Repository, video: SourceVideo) -> Path:
    """Скачать фильм, при отказе сменив выход прокси и попробовав ещё раз.

    Метаданные и сами данные YouTube отдаёт РАЗНЫМИ путями: плеер может ответить, а CDN
    следом дать `403 Forbidden` — ровно это случилось 15.08 на выходе, который проверку
    прошёл. Поэтому проверять выход заранее мало, нужен повтор по факту отказа.

    Попытка ровно одна: фильм — это сотни мегабайт и минуты работы, а бесконечные
    повторы на 1-ядерном VPS съели бы и слот, и память."""
    try:
        return download_video(video, DAILY_VIDEO_DIR)
    except Exception as first_error:  # noqa: BLE001 — граница сети, решаем сменой выхода
        current = os.environ.get("YT_PROXY", "").strip() or None
        logger.warning(
            "Фильм %s не скачался через %s (%s) — меняю выход и пробую ещё раз",
            video.ref, current or "прямой путь", str(first_error)[:80],
        )
        other = pick_working_proxy(repo, exclude=current)
        if other is None or other == current:
            raise
        os.environ["YT_PROXY"] = other
        return download_video(video, DAILY_VIDEO_DIR)


def _alert_video_failure(repo: Repository, channel_name: str, stage: str, reason: str) -> None:
    """Сказать владельцу, что фильм не вышел. Никогда не поднимает исключение и никогда
    не заменяет собой запись в журнале: тревога — способ УЗНАТЬ о поломке, а разбираться
    всё равно по журналу.

    ТЗ владельца 2026-08-14: «много тревог не надо». Поэтому ходит ОДНА тревога — про
    фильм, и не чаще раза в сутки (`VIDEO_ALERT_COOLDOWN_HOURS`). Клипы своей тревоги не
    имеют: без фильма их не бывает по построению, значит сообщение о них всегда было бы
    вторым про одну и ту же поломку."""
    if stage != "фильм":
        return
    try:
        alert_once(
            repo,
            build_video_failure_alert(channel_name, stage, reason),
            LAST_FILM_ALERT_KEY,
            cooldown_hours=VIDEO_ALERT_COOLDOWN_HOURS,
        )
    except Exception:  # noqa: BLE001 — сбой уведомления не должен ронять видео-джоб
        logger.exception("Тревога о видео не отправлена")


def _pick_video(
    repo: Repository,
    channel: Channel,
    settings: ChannelSettings,
    vk_fetcher: VKFetcher | None,
    *,
    alerts_enabled: bool = True,
) -> SourceVideo | None:
    """YouTube-каналы проверяются по порядку первыми (основной источник); VK-группа —
    только если ни один YouTube-канал не настроен (резервный путь).

    Пустой результат бывает по ДВУМ разным причинам, и владельцу нужны разные действия:
    источники не читаются (поломка — YouTube требует подтверждения «я не бот», канал
    переехал) либо все ролики в окне просмотра уже публиковались (нужен новый источник).
    Обе уходят тревогой с разным текстом; молча возвращать None нельзя — именно так
    Кино и простояло двое суток, честно печатая «новых видео нет» в журнал, который
    никто не читал."""
    reposted = repo.list_reposted_video_refs(channel.id)
    failures: list[str] = []
    for channel_url in settings.daily_video_youtube_channels:
        try:
            video = pick_unreposted_youtube(
                channel_url, reposted, count=settings.daily_video_lookback
            )
        except Exception as error:  # noqa: BLE001 — граница сети, причина уходит владельцу
            logger.exception("Видео-репост [%s]: YouTube-канал %s недоступен", channel.name, channel_url)
            failures.append(f"{channel_url}: {error}")
            continue
        if video is not None:
            return video

    if settings.daily_video_group is not None and vk_fetcher is not None:
        videos = [
            source_video_from_item(item)
            for item in vk_fetcher.fetch_group_videos(settings.daily_video_group)
        ]
        video = pick_unreposted(videos, reposted)
        if video is not None:
            return video

    if failures and alerts_enabled:
        _alert_video_failure(
            repo, channel.name, "фильм",
            "источники не читаются — " + "; ".join(failures[:3]),
        )
    elif alerts_enabled and (
        settings.daily_video_youtube_channels or settings.daily_video_group is not None
    ):
        _alert_video_failure(
            repo, channel.name, "фильм",
            f"все ролики источников уже публиковались (просмотрено по "
            f"{settings.daily_video_lookback} последних на канал, взято всего "
            f"{len(reposted)}). Нужен новый источник.",
        )
    return None


def run_daily_video_repost(
    repo: Repository,
    channel: Channel,
    *,
    vk_fetcher: VKFetcher | None,
    vk_publisher: VKPublisher,
    llm_client: LLMClient,
    footer_links: FooterLinks | None,
    tg_video_publisher: TelethonVideoPublisher | None = None,
    youtube_publisher: YouTubePublisher | None = None,
    rng: random.Random | None = None,
) -> None:
    """Полный дневной цикл одного канала. Ошибки скачивания/нарезки не откатывают уже
    сделанную публикацию видео — репост важнее клипов."""
    settings = ChannelSettings.from_json(channel.settings_json)
    has_source = settings.daily_video_youtube_channels or settings.daily_video_group is not None
    if not has_source or not channel.vk_destination:
        return

    ensure_working_proxy(repo)
    video = _pick_video(repo, channel, settings, vk_fetcher)
    if video is None:
        logger.info("Видео-репост [%s]: новых видео в источнике нет", channel.name)
        return

    # Токен проверяется ДО тяжёлой работы. Порядок шагов ниже жёсткий: фильм помечается
    # опубликованным ещё до скачивания, поэтому неудача на публикации СЪЕДАЕТ фильм —
    # он больше не выберется, и сутки останутся без кино. Ровно это и случилось 05–06.08
    # после того, как отложенная публикация (require_media) стала возвращать неуспех:
    # в логе «Видео-репост: публикация не удалась: postponed» два дня подряд.
    # Пул занят — просто уходим, ничего не пометив и не скачав: джоб вернётся через 15 мин.
    pool = (
        VkTokenPool(
            settings.vk_upload_token_envs,
            daily_cap=settings.vk_token_daily_cap or DEFAULT_DAILY_CAP,
            caller=f"film:{channel.name}",
        )
        if settings.vk_upload_token_envs
        else None
    )
    if pool is not None and not pool.has_free_account():
        logger.info(
            "Видео-репост [%s]: личный токен занят — фильм не трогаем, вернёмся позже",
            channel.name,
        )
        return

    # Место проверяется ДО отметки в БД и до скачивания. Фильм — самый прожорливый шаг
    # всего сервера (500–650 МБ плюс нарезка клипов), и качать его в упор значит получить
    # недокачанный файл, забитый диск и вставшие вместе с ним ВСЕ четыре софта: медиа в
    # VK грузится через диск. Аварийная уборка сначала пробует освободить место сама.
    if not has_free_space(DAILY_VIDEO_DIR, FILM_REQUIRED_FREE_MB):
        emergency_cleanup(OUTPUT_DIR)
    if not has_free_space(DAILY_VIDEO_DIR, FILM_REQUIRED_FREE_MB):
        logger.error(
            "Видео-репост [%s]: мало места на диске (%s) — фильм не качаем",
            channel.name, disk_status(DAILY_VIDEO_DIR),
        )
        return

    downloaded = _download_first_available(
        repo, channel, settings, vk_fetcher, video, llm_client
    )
    if downloaded is None:
        return
    video, title, description, local_file = downloaded
    try:
        body = "\n\n".join(part for part in (title, description) if part.strip())
        result = _publish_film(
            vk_publisher, channel, settings,
            video_path=local_file, title=title, body=body, footer_links=footer_links,
        )
        if not result.success:
            logger.error("Видео-репост [%s]: публикация не удалась: %s", channel.name, result.error)
            # «postponed» — это занятый личный токен VK, а не проблема ролика. Пул
            # проверяется ДО скачивания, но между проверкой и заливкой проходят минуты,
            # и слот успевает уйти другому софту. Снимаем отметку: фильм скачан зря, но
            # хотя бы не потерян — следующий заход возьмёт его снова. Тревогу тут не шлём,
            # это штатное состояние при одном аккаунте на четыре софта.
            if (result.error or "").startswith("postponed"):
                repo.forget_reposted_video(channel_id=channel.id, video_ref=video.ref)
                logger.info(
                    "Видео-репост [%s]: %s возвращён в очередь — токен был занят",
                    channel.name, video.ref,
                )
                return
            _alert_video_failure(repo, channel.name, "фильм", f"не опубликовался — {result.error}")
            return
        repo.mark_video_published(channel_id=channel.id, video_ref=video.ref)
        logger.info("Видео-репост [%s]: опубликовано %s (%s)", channel.name, video.ref, title)

        # TG-заливка идёт ПОСЛЕ отметки в БД: если она упадёт, день всё равно считается
        # закрытым и завтрашний прогон возьмёт следующий фильм, а не этот же снова.
        if tg_video_publisher is not None and channel.tg_destination:
            tg_video_publisher.publish_video(
                destination=channel.tg_destination,
                video_path=local_file,
                caption=build_film_caption(body, footer_links),
            )

        if youtube_publisher is not None and settings.youtube_upload:
            _upload_to_youtube(
                youtube_publisher, channel, settings,
                video_path=local_file, title=title or video.title, body=body, is_short=False,
            )

        _cut_and_schedule_clips(
            repo, channel, settings,
            video_ref=video.ref, video_file=local_file,
            title=title or video.title, description=description,
            llm_client=llm_client, footer_links=footer_links, rng=rng,
        )
    finally:
        local_file.unlink(missing_ok=True)  # диск VPS маленький — файл не храним


TG_CAPTION_LIMIT = 1024
"""Лимит подписи к видео в Telegram. Обрезаем ЗДЕСЬ, а не в публикаторе: там режется
хвост, то есть ровно футер со ссылками — а он владельцу и нужен (ТЗ 2026-08-10)."""


def build_film_caption(body: str, footer_links: FooterLinks | None) -> str:
    """Подпись к фильму в TG: текст + ссылки на свой канал и свою VK-группу.

    Раньше фильм уходил в Telegram голым текстом без футера — ссылки были только у
    обычных постов. ТЗ 2026-08-10: «к фильмам в тг тоже это добавляй».

    Markdown, а не HTML: Telethon разбирает подпись как markdown, HTML-теги уехали бы
    в канал сырыми."""
    footer = build_markdown_footer(footer_links) if footer_links else ""
    if not footer:
        return body[:TG_CAPTION_LIMIT]
    room = TG_CAPTION_LIMIT - len(footer) - 2
    trimmed = body.strip()[:max(room, 0)].rstrip()
    return f"{trimmed}\n\n{footer}" if trimmed else footer


def channel_seo_links(channel: Channel, settings: ChannelSettings) -> list[str]:
    """Ссылки канала для «подвала» описания ролика. Пустые поля просто не дают строки."""
    links = []
    if settings.tg_footer_url:
        links.append(f"📲 Telegram: {settings.tg_footer_url}")
    if settings.vk_footer_url:
        links.append(f"🔷 VK: {settings.vk_footer_url}")
    elif channel.vk_destination:
        links.append(f"🔷 VK: {build_vk_group_url(channel.vk_destination)}")
    return links


def build_video_description(
    channel: Channel,
    settings: ChannelSettings,
    *,
    title: str,
    body: str,
    footer_links: FooterLinks | None,
) -> str:
    """Описание ролика в каталоге сообщества.

    SEO включён — большое поисковое описание (ТЗ 2026-08-10: ролик не публикуется
    записью, его описание в ленте свёрнуто, значит место под ключи бесплатное).
    Выключен — прежний текст поста, чтобы канал без SEO-профиля ничего не заметил."""
    if not settings.seo_enabled:
        return _build_vk_publish_text(None, body, footer_links, False)
    return build_video_seo_description(
        title=title,
        body=body,
        profile=settings.seo_profile(channel_seo_links(channel, settings)),
    )


def _publish_film(
    vk_publisher: VKPublisher,
    channel: Channel,
    settings: ChannelSettings,
    *,
    video_path: Path,
    title: str,
    body: str,
    footer_links: FooterLinks | None,
) -> VKPublishResult:
    """Фильм в сообщество: либо запись на стене с вложением, либо только раздел «Видео».

    `video_as_post=False` — ТЗ владельца 2026-08-10 («пусть фильмы идут в видео»).
    Стена под фильмы больше не занимается, но интервальный гейт публикаций
    (`get_last_published_at`) двигается по-прежнему — иначе текстовый пост вышел бы
    впритык за фильмом и порядок дня «фильм → клип → посты» рассыпался бы."""
    description = build_video_description(
        channel, settings, title=title, body=body, footer_links=footer_links
    )
    if settings.video_as_post:
        return vk_publisher.publish(
            group_id=int(channel.vk_destination),
            text=_build_vk_publish_text(None, body, footer_links, False),
            video_path=video_path,
            video_title=title or None,
            video_description=description or None,
        )
    return vk_publisher.publish_video_only(
        group_id=int(channel.vk_destination),
        video_path=video_path,
        title=title or None,
        description=description or None,
    )


def _prepare_local_file(local_file: Path, settings: ChannelSettings) -> Path:
    """Единое оформление фильма (свой логотип + блюр чужого знака) до публикации и
    нарезки — клипы наследуют его автоматически. Сбой оформления не отменяет день:
    публикуем как скачали."""
    if settings.film_logo_path is None and not settings.film_blur_region:
        return local_file
    try:
        return prepare_film(
            local_file,
            logo_path=PROJECT_ROOT / settings.film_logo_path if settings.film_logo_path else None,
            blur_region=settings.film_blur_region,
            blur_strength=settings.film_blur_strength,
            video_width=probe_dimensions(local_file)[0],
        )
    except Exception:
        logger.exception("Оформление фильма не удалось, публикую исходник")
        return local_file


def _cut_and_schedule_clips(
    repo: Repository,
    channel: Channel,
    settings: ChannelSettings,
    *,
    video_ref: str,
    video_file: Path,
    title: str,
    description: str,
    llm_client: LLMClient,
    footer_links: FooterLinks | None,
    rng: random.Random | None,
) -> None:
    hooks = generate_clip_hooks(
        llm_client, title=title, description=description, count=settings.daily_clip_count
    )
    logo_path = PROJECT_ROOT / settings.clip_logo_path if settings.clip_logo_path else None
    try:
        cuts = cut_clips(
            video_file,
            title=title,
            out_dir=CLIPS_DIR,
            clip_seconds=settings.daily_clip_seconds,
            count=settings.daily_clip_count,
            existing_intervals=repo.list_clip_intervals(video_ref),
            min_gap_seconds=settings.daily_clip_min_gap_seconds,
            headlines=hooks,
            logo_path=logo_path,
            rng=rng,
        )
    except Exception as error:  # noqa: BLE001 — граница ffmpeg, причина уходит владельцу
        logger.exception("Видео-репост [%s]: нарезка клипов упала", channel.name)
        _alert_video_failure(repo, channel.name, "клипы", f"нарезка упала — {error}")
        return

    clip_text = _build_vk_publish_text(None, title, footer_links, False)
    times = plan_clip_times(datetime.datetime.utcnow(), len(cuts), rng=rng)
    for cut, moment in zip(cuts, times):
        repo.create_clip_segment(
            channel_id=channel.id,
            video_ref=video_ref,
            start_seconds=cut.start_seconds,
            end_seconds=cut.end_seconds,
            clip_path=str(cut.path),
            text=clip_text,
            scheduled_at=moment,
        )
        logger.info(
            "Клип запланирован [%s]: %s на %s UTC", channel.name, cut.path.name, moment
        )


_CLIP_TIMESTAMP_SUFFIX = re.compile(r"_\d{8}_\d{6}$")


def _clip_title_from_path(clip_path: Path) -> str:
    """Имя клипа «Название фильма_YYYYMMDD_HHMMSS» → «Название фильма» (заголовок для
    YouTube Shorts; таймстамп в файле нужен для уникальности имени, не для зрителя)."""
    return _CLIP_TIMESTAMP_SUFFIX.sub("", clip_path.stem) or clip_path.stem


def _upload_to_youtube(
    youtube_publisher: YouTubePublisher,
    channel: Channel,
    settings: ChannelSettings,
    *,
    video_path: Path,
    title: str,
    body: str,
    is_short: bool,
) -> None:
    yt_title = build_youtube_title(title, is_short=is_short)
    yt_description = build_youtube_description(
        body,
        vk_url=build_vk_group_url(channel.vk_destination),
        tg_url=settings.tg_footer_url,
    )
    youtube_publisher.upload(
        video_path, title=yt_title, description=yt_description, is_short=is_short
    )


def publish_due_clips(
    repo: Repository,
    *,
    vk_publisher_for,
    youtube_publisher: YouTubePublisher | None = None,
    now: datetime.datetime | None = None,
) -> None:
    """Опубликовать клипы, чьё время пришло. vk_publisher_for(channel) → VKPublisher|None
    (инжект — тестируется без сети). Неудача — клип остаётся в плане до следующего
    прогона; старше CLIP_EXPIRE_HOURS — снимается с плана, файл удаляется."""
    now = now or datetime.datetime.utcnow()
    for clip in repo.list_due_clips(now):
        channel = repo.get_channel(clip.channel_id)
        clip_path = Path(clip.clip_path) if clip.clip_path else None

        expired = now - clip.scheduled_at > datetime.timedelta(hours=CLIP_EXPIRE_HOURS)
        file_missing = clip_path is None or not clip_path.exists()
        if expired or file_missing or channel is None or not channel.vk_destination:
            cause = "протух" if expired else "файл/канал недоступен"
            logger.warning("Клип %d снят с плана (%s)", clip.id, cause)
            # Снятый клип — это ровно та жалоба «клипов нет», которую нечем было увидеть:
            # он тихо исчезал из плана, а стена жила текстовыми постами.
            _alert_video_failure(
                repo, channel.name if channel is not None else "Кино", "клипы",
                f"клип {clip.id} снят с плана — {cause}",
            )
            repo.mark_clip_published(clip.id)
            if clip_path is not None:
                clip_path.unlink(missing_ok=True)
            continue

        publisher = vk_publisher_for(channel)
        if publisher is None:
            logger.warning("Клип %d: publisher канала %s недоступен", clip.id, channel.name)
            continue
        settings = ChannelSettings.from_json(channel.settings_json)
        if settings.video_as_post:
            result = publisher.publish(
                group_id=int(channel.vk_destination),
                text=clip.text or "",
                video_path=clip_path,
                video_title=clip_path.stem,
            )
        else:
            # ТЗ 2026-08-10: «клипы в клипы» — записи на стене не создаём, ролик уходит
            # в раздел коротких видео сообщества со своим (большим) описанием.
            result = publisher.publish_video_only(
                group_id=int(channel.vk_destination),
                video_path=clip_path,
                title=_clip_title_from_path(clip_path),
                description=build_video_description(
                    channel, settings,
                    title=_clip_title_from_path(clip_path),
                    body=clip.text or "",
                    footer_links=None,
                ),
                as_clip=True,
            )
        if result.success:
            # YouTube Shorts — до удаления файла (клип грузится только раз, best-effort).
            if youtube_publisher is not None and settings.youtube_upload:
                _upload_to_youtube(
                    youtube_publisher, channel, settings,
                    video_path=clip_path, title=_clip_title_from_path(clip_path),
                    body=clip.text or "", is_short=True,
                )
            repo.mark_clip_published(clip.id)
            clip_path.unlink(missing_ok=True)
            logger.info("Клип %d опубликован в VK (канал %s)", clip.id, channel.name)
        else:
            logger.error("Клип %d: публикация не удалась: %s", clip.id, result.error)
