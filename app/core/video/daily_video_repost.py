"""Ежедневный видео-репост: одно видео из VK-группы-источника → наш канал + клипы.

Раз в день (для каналов с настройкой daily_video_group): берём самое свежее ещё не
публиковавшееся видео источника, скачиваем (480/720p), AI переписывает название и
описание (если они есть), публикуем в наш канал видеозаписью + постом с видео, режем
файл на N вертикальных клипов и планируем их публикацию в случайное время по остатку
дня, после чего скачанный файл удаляется с диска. Публикация клипов — отдельным
частым джобом по плану из БД (переживает рестарт сервиса).
"""
from __future__ import annotations

import datetime
import logging
import random
from pathlib import Path

from app.core.channel_settings import ChannelSettings
from app.core.llm.client import LLMClient
from app.core.llm.video_rewriter import rewrite_video_texts
from app.core.monitoring.vk_fetcher import VKFetcher
from app.core.publishing.footer import FooterLinks
from app.core.publishing.vk_publisher import VKPublisher
from app.core.publishing.vk_queue_service import _build_vk_publish_text
from app.core.video.clip_cutter import cut_clips
from app.core.video.video_source import download_video, pick_unreposted, source_video_from_item
from app.db.models import Channel
from app.db.repository import Repository
from app.paths import OUTPUT_DIR

logger = logging.getLogger("publishing")

DAILY_VIDEO_DIR = OUTPUT_DIR / "daily_video"
CLIPS_DIR = OUTPUT_DIR / "clips"
# Окно публикации клипов — до 20:00 UTC (23:00 МСК): позже аудитория спит.
CLIP_WINDOW_END_HOUR_UTC = 20
CLIP_MIN_SPACING_MINUTES = 45
# Клип, который не удалось опубликовать сутки, считаем протухшим — не долбим VK вечно.
CLIP_EXPIRE_HOURS = 24


def plan_clip_times(
    now: datetime.datetime,
    count: int,
    *,
    end_hour_utc: int = CLIP_WINDOW_END_HOUR_UTC,
    min_spacing_minutes: int = CLIP_MIN_SPACING_MINUTES,
    rng: random.Random | None = None,
) -> list[datetime.datetime]:
    """Случайные моменты публикации клипов в остатке дня (от now+30мин до end_hour_utc),
    не ближе min_spacing друг к другу. Если окно дня уже кончилось — просто раскладываем
    с шагом min_spacing от текущего момента (клипы уйдут вечером, а не потеряются)."""
    rng = rng or random.Random()
    spacing = datetime.timedelta(minutes=min_spacing_minutes)
    start = now + datetime.timedelta(minutes=30)
    end = now.replace(hour=end_hour_utc, minute=0, second=0, microsecond=0)

    if end <= start + spacing * count:
        return [start + spacing * i for i in range(count)]

    offsets = sorted(rng.uniform(0, (end - start).total_seconds()) for _ in range(count))
    times: list[datetime.datetime] = []
    for offset in offsets:
        moment = start + datetime.timedelta(seconds=offset)
        if times and moment - times[-1] < spacing:
            moment = times[-1] + spacing
        times.append(moment)
    return times


def run_daily_video_repost(
    repo: Repository,
    channel: Channel,
    *,
    vk_fetcher: VKFetcher,
    vk_publisher: VKPublisher,
    llm_client: LLMClient,
    footer_links: FooterLinks | None,
    rng: random.Random | None = None,
) -> None:
    """Полный дневной цикл одного канала. Ошибки скачивания/нарезки не откатывают уже
    сделанную публикацию видео — репост важнее клипов."""
    settings = ChannelSettings.from_json(channel.settings_json)
    if settings.daily_video_group is None or not channel.vk_destination:
        return

    videos = [
        source_video_from_item(item)
        for item in vk_fetcher.fetch_group_videos(settings.daily_video_group)
    ]
    video = pick_unreposted(videos, repo.list_reposted_video_refs(channel.id))
    if video is None:
        logger.info("Видео-репост [%s]: новых видео в источнике нет", channel.name)
        return

    title, description = rewrite_video_texts(
        llm_client, title=video.title, description=video.description
    )
    local_file = download_video(video, DAILY_VIDEO_DIR)
    try:
        body = "\n\n".join(part for part in (title, description) if part.strip())
        text = _build_vk_publish_text(None, body, footer_links, False)
        result = vk_publisher.publish(
            group_id=int(channel.vk_destination),
            text=text,
            video_path=local_file,
            video_title=title or None,
            video_description=description or None,
        )
        if not result.success:
            logger.error("Видео-репост [%s]: публикация не удалась: %s", channel.name, result.error)
            return
        repo.add_reposted_video(channel_id=channel.id, video_ref=video.ref, title=title or None)
        logger.info("Видео-репост [%s]: опубликовано %s (%s)", channel.name, video.ref, title)

        _cut_and_schedule_clips(
            repo, channel, settings,
            video_ref=video.ref, video_file=local_file,
            title=title or video.title, footer_links=footer_links, rng=rng,
        )
    finally:
        local_file.unlink(missing_ok=True)  # диск VPS маленький — файл не храним


def _cut_and_schedule_clips(
    repo: Repository,
    channel: Channel,
    settings: ChannelSettings,
    *,
    video_ref: str,
    video_file: Path,
    title: str,
    footer_links: FooterLinks | None,
    rng: random.Random | None,
) -> None:
    try:
        cuts = cut_clips(
            video_file,
            title=title,
            out_dir=CLIPS_DIR,
            clip_seconds=settings.daily_clip_seconds,
            count=settings.daily_clip_count,
            existing_intervals=repo.list_clip_intervals(video_ref),
            min_gap_seconds=settings.daily_clip_min_gap_seconds,
            rng=rng,
        )
    except Exception:
        logger.exception("Видео-репост [%s]: нарезка клипов упала", channel.name)
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


def publish_due_clips(
    repo: Repository,
    *,
    vk_publisher_for,
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
            logger.warning(
                "Клип %d снят с плана (%s)", clip.id,
                "протух" if expired else "файл/канал недоступен",
            )
            repo.mark_clip_published(clip.id)
            if clip_path is not None:
                clip_path.unlink(missing_ok=True)
            continue

        publisher = vk_publisher_for(channel)
        if publisher is None:
            logger.warning("Клип %d: publisher канала %s недоступен", clip.id, channel.name)
            continue
        result = publisher.publish(
            group_id=int(channel.vk_destination),
            text=clip.text or "",
            video_path=clip_path,
            video_title=clip_path.stem,
        )
        if result.success:
            repo.mark_clip_published(clip.id)
            clip_path.unlink(missing_ok=True)
            logger.info("Клип %d опубликован в VK (канал %s)", clip.id, channel.name)
        else:
            logger.error("Клип %d: публикация не удалась: %s", clip.id, result.error)
