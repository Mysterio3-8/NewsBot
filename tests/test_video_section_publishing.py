"""Фильм → раздел «Видео», клип → «Клипы», записи на стене нет (ТЗ 2026-08-10)."""
import datetime
from pathlib import Path

from app.core.channel_settings import ChannelSettings
from app.core.publishing.footer import FooterLinks
from app.core.publishing.vk_publisher import VKPublishResult
from app.core.video.daily_video_repost import (
    TG_CAPTION_LIMIT,
    build_film_caption,
    build_video_description,
    channel_seo_links,
    publish_due_clips,
)
from app.db.repository import Repository, init_db, make_engine


class RecordingPublisher:
    """Различает два пути публикации: запись на стене и заливка в раздел."""

    def __init__(self):
        self.wall_calls = []
        self.section_calls = []

    def publish(self, **kwargs):
        self.wall_calls.append(kwargs)
        return VKPublishResult(success=True, post_id=len(self.wall_calls), error=None)

    def publish_video_only(self, **kwargs):
        self.section_calls.append(kwargs)
        return VKPublishResult(
            success=True, post_id=None, error=None, attachment="video-1_1"
        )


def _repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "sections.db")
    init_db(engine)
    return Repository(engine)


def _channel(repo, **overrides):
    settings = ChannelSettings(**overrides)
    return repo.create_channel(
        name="Кино", vk_destination="240120678", settings_json=settings.to_json()
    )


def _seo_settings(**overrides) -> ChannelSettings:
    base = dict(
        seo_enabled=True,
        seo_hashtag_group="kinobestfilmss",
        seo_base_tags=["кино"],
        seo_search_phrases=["{q} смотреть онлайн"],
        tg_footer_url="https://t.me/kinobestfilmss",
        video_as_post=False,
    )
    base.update(overrides)
    return ChannelSettings(**base)


def _schedule_clip(repo, channel, tmp_path) -> Path:
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"x")
    repo.create_clip_segment(
        channel_id=channel.id,
        video_ref="ref-1",
        start_seconds=0,
        end_seconds=35,
        clip_path=str(clip_path),
        text="Детектив Стая",
        scheduled_at=datetime.datetime.utcnow() - datetime.timedelta(minutes=1),
    )
    return clip_path


def test_clip_goes_to_clips_section_without_wall_post(tmp_path):
    repo = _repo(tmp_path)
    channel = _channel(repo, video_as_post=False)
    _schedule_clip(repo, channel, tmp_path)
    publisher = RecordingPublisher()

    publish_due_clips(repo, vk_publisher_for=lambda _: publisher)

    assert publisher.wall_calls == []
    assert len(publisher.section_calls) == 1
    assert publisher.section_calls[0]["as_clip"] is True


def test_clip_still_goes_to_the_wall_when_channel_keeps_old_behaviour(tmp_path):
    repo = _repo(tmp_path)
    channel = _channel(repo)  # video_as_post по умолчанию True
    _schedule_clip(repo, channel, tmp_path)
    publisher = RecordingPublisher()

    publish_due_clips(repo, vk_publisher_for=lambda _: publisher)

    assert len(publisher.wall_calls) == 1
    assert publisher.section_calls == []


def test_published_clip_file_is_removed(tmp_path):
    repo = _repo(tmp_path)
    channel = _channel(repo, video_as_post=False)
    clip_path = _schedule_clip(repo, channel, tmp_path)

    publish_due_clips(repo, vk_publisher_for=lambda _: RecordingPublisher())

    assert not clip_path.exists()


class _FakeChannel:
    vk_destination = "240120678"


def test_video_description_is_seo_when_enabled():
    description = build_video_description(
        _FakeChannel(), _seo_settings(),
        title="Детектив Стая",
        body="Роберт Дауни играет детектива.",
        footer_links=None,
    )

    assert description.startswith("Детектив Стая")
    assert "смотреть онлайн" in description
    assert "https://t.me/kinobestfilmss" in description
    assert "#кино@kinobestfilmss" in description


def test_video_description_falls_back_to_post_text_without_seo():
    description = build_video_description(
        _FakeChannel(), ChannelSettings(video_as_post=False),
        title="Детектив Стая",
        body="Обычный текст поста.",
        footer_links=None,
    )

    assert description.strip() == "Обычный текст поста."


def test_channel_links_use_vk_destination_when_footer_url_missing():
    links = channel_seo_links(_FakeChannel(), _seo_settings(vk_footer_url=None))
    assert any("240120678" in link for link in links)


KINO_FOOTER = FooterLinks(
    telegram_url="https://t.me/kinobestfilmss",
    telegram_signature="🎬 Больше фильмов",
    vk_url="https://vk.com/public240120678",
)


def test_film_caption_carries_both_links():
    """ТЗ 2026-08-10: «к фильмам в тг тоже это добавляй» — TG-канал и VK-группа."""
    caption = build_film_caption("Детектив Стая. Описание фильма.", KINO_FOOTER)

    assert "[🎬 Больше фильмов](https://t.me/kinobestfilmss)" in caption
    assert "[🔵 Больше контента в нашем VK](https://vk.com/public240120678)" in caption


def test_film_caption_trims_body_not_the_links():
    """Публикатор режет хвост подписи по лимиту 1024 — то есть ровно футер.
    Поэтому режем здесь и режем ТЕКСТ."""
    caption = build_film_caption("слово " * 1000, KINO_FOOTER)

    assert len(caption) <= TG_CAPTION_LIMIT
    assert caption.rstrip().endswith("(https://vk.com/public240120678)")


def test_film_caption_without_footer_is_plain_body():
    assert build_film_caption("Просто описание", None) == "Просто описание"
