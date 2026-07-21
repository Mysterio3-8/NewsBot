"""Ежедневный видео-репост: оркестрация, план клипов по дню, публикатор due-клипов."""
import datetime
import random
from pathlib import Path
from unittest.mock import patch

from app.core.channel_settings import ChannelSettings
from app.core.publishing.vk_publisher import VKPublishResult
from app.core.video.clip_cutter import ClipCut
from app.core.video.daily_video_repost import (
    plan_clip_times,
    publish_due_clips,
    run_daily_video_repost,
)
from app.core.video.video_source import SourceVideo
from app.db.repository import Repository, init_db, make_engine


class FakeFetcher:
    def __init__(self, items):
        self._items = items

    def fetch_group_videos(self, group_id, *, count=100):
        return self._items


class FakePublisher:
    def __init__(self, success=True):
        self.success = success
        self.calls = []

    def publish(self, **kwargs):
        self.calls.append(kwargs)
        if not self.success:
            return VKPublishResult(success=False, post_id=None, error="boom")
        return VKPublishResult(success=True, post_id=len(self.calls), error=None)


def _repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "daily.db")
    init_db(engine)
    return Repository(engine)


def _kino_channel(repo, *, group=223779047):
    """Канал на резервном VK-пути (без youtube-каналов) — фолбэк на daily_video_group."""
    settings = ChannelSettings(daily_video_group=group, daily_clip_count=2)
    return repo.create_channel(
        name="Кино", vk_destination="240120678", settings_json=settings.to_json()
    )


def _kino_channel_youtube(repo, *, channels=("https://www.youtube.com/@mmalive1830",), group=None):
    settings = ChannelSettings(
        daily_video_youtube_channels=list(channels), daily_video_group=group, daily_clip_count=2
    )
    return repo.create_channel(
        name="Кино", vk_destination="240120678", settings_json=settings.to_json()
    )


def _vk_item(video_id, title="Интерстеллар"):
    return {
        "id": video_id,
        "owner_id": -223779047,
        "title": title,
        "description": "Описание фильма",
        "duration": 7200,
        "files": {"mp4_480": "http://cdn/480.mp4"},
    }


# --- plan_clip_times ---

def test_plan_clip_times_spaced_by_random_interval():
    now = datetime.datetime(2026, 7, 18, 9, 0)
    times = plan_clip_times(now, 3, rng=random.Random(5))

    assert len(times) == 3
    assert times == sorted(times)
    assert now + datetime.timedelta(minutes=30) <= times[0] <= now + datetime.timedelta(minutes=90)
    for earlier, later in zip(times, times[1:]):
        gap = later - earlier
        assert datetime.timedelta(minutes=90) <= gap <= datetime.timedelta(minutes=240)


def test_plan_clip_times_keeps_going_past_midnight():
    """Круглосуточная публикация (ТЗ 2026-07-21): поздний вечер больше не сжимает план."""
    now = datetime.datetime(2026, 7, 18, 21, 30)
    times = plan_clip_times(now, 3, rng=random.Random(5))

    assert times[-1].day == 19
    for earlier, later in zip(times, times[1:]):
        assert later - earlier >= datetime.timedelta(minutes=90)


# --- run_daily_video_repost ---

class FakeTelethonPublisher:
    def __init__(self, success=True):
        self.success = success
        self.calls = []

    def publish_video(self, **kwargs):
        self.calls.append(kwargs)
        return self.success


def _run(
    repo, channel, fetcher, publisher, tmp_path,
    *, cuts=None, youtube_video=None, tg_video_publisher=None,
):
    downloaded = tmp_path / "film.mp4"

    def fake_download(video, dest_dir, **kwargs):
        downloaded.write_bytes(b"video")
        return downloaded

    def fake_cut_clips(video_path, **kwargs):
        result = []
        for index, (start, end) in enumerate(cuts or []):
            clip = tmp_path / f"clip_{index}.mp4"
            clip.write_bytes(b"clip")
            result.append(ClipCut(start_seconds=start, end_seconds=end, path=clip))
        return result

    youtube_patch = (
        patch("app.core.video.daily_video_repost.pick_unreposted_youtube", return_value=youtube_video)
        if youtube_video is not None
        else patch("app.core.video.daily_video_repost.pick_unreposted_youtube")
    )

    with patch("app.core.video.daily_video_repost.download_video", side_effect=fake_download), \
         patch("app.core.video.daily_video_repost.cut_clips", side_effect=fake_cut_clips), \
         patch("app.core.video.daily_video_repost.rewrite_video_texts",
               return_value=("Новое название", "Новое описание")), \
         youtube_patch as youtube_mock:
        if youtube_video is None:
            youtube_mock.return_value = None  # по умолчанию youtube пуст — идём в VK-фолбэк
        run_daily_video_repost(
            repo, channel,
            vk_fetcher=fetcher, vk_publisher=publisher,
            llm_client=None, footer_links=None, rng=random.Random(1),
            tg_video_publisher=tg_video_publisher,
        )
    return downloaded


def test_clips_are_cut_with_channel_logo_and_ai_hooks(tmp_path):
    repo = _repo(tmp_path)
    settings = ChannelSettings(
        daily_video_group=223779047, daily_clip_count=2, clip_logo_path="assets/filmlogo.png"
    )
    channel = repo.create_channel(
        name="Кино", vk_destination="240120678", settings_json=settings.to_json()
    )
    captured = {}

    def fake_cut_clips(video_path, **kwargs):
        captured.update(kwargs)
        return []

    with patch("app.core.video.daily_video_repost.download_video",
               side_effect=lambda video, dest_dir, **kw: _written(tmp_path / "film.mp4")), \
         patch("app.core.video.daily_video_repost.cut_clips", side_effect=fake_cut_clips), \
         patch("app.core.video.daily_video_repost.rewrite_video_texts",
               return_value=("Астрал", "Хоррор")), \
         patch("app.core.video.daily_video_repost.generate_clip_hooks",
               return_value=["Хук раз", "Хук два"]), \
         patch("app.core.video.daily_video_repost.pick_unreposted_youtube", return_value=None):
        run_daily_video_repost(
            repo, channel,
            vk_fetcher=FakeFetcher([_vk_item(10)]), vk_publisher=FakePublisher(),
            llm_client=None, footer_links=None, rng=random.Random(1),
        )

    assert captured["headlines"] == ["Хук раз", "Хук два"]
    assert captured["logo_path"].name == "filmlogo.png"


def _written(path: Path) -> Path:
    path.write_bytes(b"video")
    return path


def test_film_is_uploaded_to_telegram_when_channel_has_tg_destination(tmp_path):
    repo = _repo(tmp_path)
    channel = _kino_channel(repo)
    repo.update_channel(channel.id, tg_destination="@kinobestfilmss")
    channel = repo.get_channel(channel.id)
    tg_publisher = FakeTelethonPublisher()

    _run(
        repo, channel, FakeFetcher([_vk_item(10)]), FakePublisher(), tmp_path,
        tg_video_publisher=tg_publisher,
    )

    assert len(tg_publisher.calls) == 1
    assert tg_publisher.calls[0]["destination"] == "@kinobestfilmss"
    assert "Новое название" in tg_publisher.calls[0]["caption"]


def test_film_is_not_uploaded_to_telegram_without_destination(tmp_path):
    repo = _repo(tmp_path)
    channel = _kino_channel(repo)
    tg_publisher = FakeTelethonPublisher()

    _run(
        repo, channel, FakeFetcher([_vk_item(10)]), FakePublisher(), tmp_path,
        tg_video_publisher=tg_publisher,
    )

    assert tg_publisher.calls == []


def test_failed_vk_publish_skips_telegram_upload(tmp_path):
    repo = _repo(tmp_path)
    channel = _kino_channel(repo)
    repo.update_channel(channel.id, tg_destination="@kinobestfilmss")
    channel = repo.get_channel(channel.id)
    tg_publisher = FakeTelethonPublisher()

    _run(
        repo, channel, FakeFetcher([_vk_item(10)]), FakePublisher(success=False), tmp_path,
        tg_video_publisher=tg_publisher,
    )

    assert tg_publisher.calls == []


def test_full_daily_cycle_publishes_records_and_cleans_up(tmp_path):
    repo = _repo(tmp_path)
    channel = _kino_channel(repo)
    publisher = FakePublisher()

    downloaded = _run(
        repo, channel, FakeFetcher([_vk_item(10)]), publisher, tmp_path,
        cuts=[(100.0, 135.0), (900.0, 935.0)],
    )

    # Видео опубликовано с AI-название/описанием и записано в дедуп.
    assert publisher.calls[0]["video_title"] == "Новое название"
    assert "Новое название" in publisher.calls[0]["text"]
    assert repo.list_reposted_video_refs(channel.id) == {"-223779047_10"}
    # Клипы запланированы с интервалами (защита от повторной нарезки тех же участков).
    assert repo.list_clip_intervals("-223779047_10") == [(100.0, 135.0), (900.0, 935.0)]
    # Скачанный фильм удалён с диска (ТЗ: не занимать место).
    assert not downloaded.exists()


def test_second_run_skips_already_reposted_video(tmp_path):
    repo = _repo(tmp_path)
    channel = _kino_channel(repo)
    fetcher = FakeFetcher([_vk_item(10)])

    _run(repo, channel, fetcher, FakePublisher(), tmp_path)
    publisher2 = FakePublisher()
    _run(repo, channel, fetcher, publisher2, tmp_path)

    assert publisher2.calls == []  # единственное видео уже публиковалось — пропуск


def test_failed_publish_not_recorded_and_file_removed(tmp_path):
    repo = _repo(tmp_path)
    channel = _kino_channel(repo)

    downloaded = _run(repo, channel, FakeFetcher([_vk_item(10)]), FakePublisher(success=False), tmp_path)

    assert repo.list_reposted_video_refs(channel.id) == set()  # завтра попробуем снова
    assert not downloaded.exists()


def _youtube_video(video_id="abc123", title="КЗК 2026", description="Боевик про кадетов"):
    return SourceVideo(
        ref=f"youtube_{video_id}",
        title=title,
        description=description,
        duration_seconds=5400,
        direct_urls={},
        page_url=f"https://www.youtube.com/watch?v={video_id}",
    )


def test_youtube_source_publishes_and_dedups_by_youtube_ref(tmp_path):
    """Основной сценарий (запрос пользователя 2026-07-18): видео берётся с YouTube-канала,
    не из VK — VK жёстко троттлит видео-CDN для датацентр-IP."""
    repo = _repo(tmp_path)
    channel = _kino_channel_youtube(repo)
    publisher = FakePublisher()

    downloaded = _run(
        repo, channel, fetcher=None, publisher=publisher, tmp_path=tmp_path,
        cuts=[(100.0, 135.0)], youtube_video=_youtube_video(),
    )

    assert publisher.calls[0]["video_title"] == "Новое название"
    assert repo.list_reposted_video_refs(channel.id) == {"youtube_abc123"}
    assert not downloaded.exists()


def test_youtube_preferred_over_vk_when_both_configured(tmp_path):
    """Если заданы и youtube-каналы, и daily_video_group — youtube в приоритете, VK
    fetcher вообще не должен дёргаться."""
    repo = _repo(tmp_path)
    channel = _kino_channel_youtube(repo, group=223779047)

    class ExplodingFetcher:
        def fetch_group_videos(self, group_id, *, count=100):
            raise AssertionError("VK не должен вызываться, когда youtube даёт видео")

    publisher = FakePublisher()
    _run(
        repo, channel, fetcher=ExplodingFetcher(), publisher=publisher, tmp_path=tmp_path,
        youtube_video=_youtube_video(),
    )

    assert repo.list_reposted_video_refs(channel.id) == {"youtube_abc123"}


def test_falls_back_to_vk_when_youtube_has_no_new_video(tmp_path):
    """Youtube настроен, но новых видео нет (всё уже опубликовано / канал недоступен) —
    и задана VK-группа — используется VK-фолбэк."""
    repo = _repo(tmp_path)
    channel = _kino_channel_youtube(repo, group=223779047)
    publisher = FakePublisher()

    _run(
        repo, channel, fetcher=FakeFetcher([_vk_item(10)]), publisher=publisher, tmp_path=tmp_path,
        youtube_video=None,
    )

    assert repo.list_reposted_video_refs(channel.id) == {"-223779047_10"}


def test_channel_without_daily_video_group_is_ignored(tmp_path):
    repo = _repo(tmp_path)
    channel = repo.create_channel(name="Новости", vk_destination="1", settings_json="{}")
    publisher = FakePublisher()

    run_daily_video_repost(
        repo, channel,
        vk_fetcher=FakeFetcher([_vk_item(10)]), vk_publisher=publisher,
        llm_client=None, footer_links=None,
    )

    assert publisher.calls == []


# --- publish_due_clips ---

def _schedule_clip(repo, channel, tmp_path, *, minutes_ago, name="clip.mp4"):
    clip_file = tmp_path / name
    clip_file.write_bytes(b"clip")
    return repo.create_clip_segment(
        channel_id=channel.id,
        video_ref="-223779047_10",
        start_seconds=10.0,
        end_seconds=45.0,
        clip_path=str(clip_file),
        text="Интерстеллар",
        scheduled_at=datetime.datetime.utcnow() - datetime.timedelta(minutes=minutes_ago),
    ), clip_file


def test_due_clip_published_marked_and_file_deleted(tmp_path):
    repo = _repo(tmp_path)
    channel = _kino_channel(repo)
    clip, clip_file = _schedule_clip(repo, channel, tmp_path, minutes_ago=5)
    publisher = FakePublisher()

    publish_due_clips(repo, vk_publisher_for=lambda ch: publisher)

    assert len(publisher.calls) == 1
    assert publisher.calls[0]["text"] == "Интерстеллар"
    assert repo.list_due_clips(datetime.datetime.utcnow()) == []  # помечен опубликованным
    assert not clip_file.exists()


def test_future_clip_not_published_yet(tmp_path):
    repo = _repo(tmp_path)
    channel = _kino_channel(repo)
    _schedule_clip(repo, channel, tmp_path, minutes_ago=-60)  # через час
    publisher = FakePublisher()

    publish_due_clips(repo, vk_publisher_for=lambda ch: publisher)

    assert publisher.calls == []


def test_failed_clip_stays_in_plan_for_retry(tmp_path):
    repo = _repo(tmp_path)
    channel = _kino_channel(repo)
    _, clip_file = _schedule_clip(repo, channel, tmp_path, minutes_ago=5)

    publish_due_clips(repo, vk_publisher_for=lambda ch: FakePublisher(success=False))

    assert len(repo.list_due_clips(datetime.datetime.utcnow())) == 1  # ретрай следующим прогоном
    assert clip_file.exists()


def test_expired_clip_removed_from_plan(tmp_path):
    repo = _repo(tmp_path)
    channel = _kino_channel(repo)
    _, clip_file = _schedule_clip(repo, channel, tmp_path, minutes_ago=60 * 25)  # сутки+
    publisher = FakePublisher()

    publish_due_clips(repo, vk_publisher_for=lambda ch: publisher)

    assert publisher.calls == []  # протухший клип не постим и не держим в плане
    assert repo.list_due_clips(datetime.datetime.utcnow()) == []
    assert not clip_file.exists()
