"""Провал фильма не должен молча съедать сутки.

Жалоба владельца 2026-08-13: «в кино фильмы и клипы не публикуются, только посты».
Механика была такая: видео помечается взятым ДО скачивания (защита от бесконечной
перекачки), поэтому ЛЮБАЯ осечка на тяжёлом шаге закрывала дневной лимит — и день
проходил без фильма. Увидеть это было нечем: стена жила текстовыми постами, сторож
тишины молчал, юнит-тесты были зелёными.
"""
import datetime
import random
from unittest.mock import patch

from app.core.channel_settings import ChannelSettings
from app.core.publishing.vk_publisher import VKPublishResult
from app.core.video.clip_cutter import ClipCut
from app.core.video.daily_video_repost import publish_due_clips, run_daily_video_repost
from app.db.repository import Repository, init_db, make_engine


class FakeFetcher:
    def __init__(self, items):
        self._items = items

    def fetch_group_videos(self, group_id, *, count=100):
        return self._items


class FakePublisher:
    def __init__(self, success=True, error="boom"):
        self.success = success
        self.error = error
        self.calls = []

    def publish(self, **kwargs):
        self.calls.append(kwargs)
        if not self.success:
            return VKPublishResult(success=False, post_id=None, error=self.error)
        return VKPublishResult(success=True, post_id=len(self.calls), error=None)


def _repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "failure.db")
    init_db(engine)
    return Repository(engine)


def _kino_channel(repo, *, clips=0):
    settings = ChannelSettings(daily_video_group=223779047, daily_clip_count=clips)
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


def _run(repo, channel, publisher, tmp_path, *, download=None, cut=None):
    """Прогон дневного видео-джоба с подменёнными тяжёлыми шагами. Возвращает
    список отправленных владельцу тревог."""
    alerts: list[tuple[str, str]] = []

    def fake_download(video, dest_dir, **kwargs):
        path = tmp_path / "film.mp4"
        path.write_bytes(b"video")
        return path

    def fake_cut(video_path, **kwargs):
        return []

    with patch("app.core.video.daily_video_repost.download_video",
               side_effect=download or fake_download), \
         patch("app.core.video.daily_video_repost.cut_clips", side_effect=cut or fake_cut), \
         patch("app.core.video.daily_video_repost.rewrite_video_texts",
               return_value=("Новое название", "Новое описание")), \
         patch("app.core.video.daily_video_repost.generate_clip_hooks", return_value=[]), \
         patch("app.core.video.daily_video_repost.pick_unreposted_youtube", return_value=None), \
         patch("app.core.video.daily_video_repost.alert_once",
               side_effect=lambda repo_, text, key, **kw: alerts.append((key, text)) or True):
        run_daily_video_repost(
            repo, channel,
            vk_fetcher=FakeFetcher([_vk_item(10)]), vk_publisher=publisher,
            llm_client=None, footer_links=None, rng=random.Random(1),
        )
    return alerts


def _today() -> datetime.datetime:
    now = datetime.datetime.utcnow()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def test_published_film_closes_the_day(tmp_path):
    repo = _repo(tmp_path)
    channel = _kino_channel(repo)

    alerts = _run(repo, channel, FakePublisher(), tmp_path)

    assert repo.count_reposted_videos_since(channel.id, _today()) == 1
    assert alerts == []


def test_failed_download_does_not_close_the_day(tmp_path):
    """Главная поломка: YouTube отвечает «подтвердите, что вы не бот» — и сутки уходили
    без фильма, потому что видео уже было помечено взятым."""
    repo = _repo(tmp_path)
    channel = _kino_channel(repo)

    def boom(video, dest_dir, **kwargs):
        raise RuntimeError("Sign in to confirm you're not a bot")

    alerts = _run(repo, channel, FakePublisher(), tmp_path, download=boom)

    assert repo.count_reposted_videos_since(channel.id, _today()) == 0
    # но само видео помечено — в цикл перекачки того же файла не уходим
    assert repo.list_reposted_video_refs(channel.id) == {"-223779047_10"}
    assert len(alerts) == 1
    assert "not a bot" in alerts[0][1]


def test_failed_publish_does_not_close_the_day(tmp_path):
    repo = _repo(tmp_path)
    channel = _kino_channel(repo)

    alerts = _run(repo, channel, FakePublisher(success=False, error="[214]"), tmp_path)

    assert repo.count_reposted_videos_since(channel.id, _today()) == 0
    assert len(alerts) == 1
    assert "[214]" in alerts[0][1]


def test_clip_failure_does_not_alert(tmp_path):
    """ТЗ 2026-08-14 «много тревог не надо»: про клипы отдельной тревоги нет.

    Без фильма клипов не бывает по построению, поэтому сообщение о них всегда было бы
    вторым про одну и ту же поломку. День при этом закрыт — фильм-то на стене."""
    repo = _repo(tmp_path)
    channel = _kino_channel(repo, clips=2)

    def boom(video_path, **kwargs):
        raise RuntimeError("ffmpeg упал")

    alerts = _run(repo, channel, FakePublisher(), tmp_path, cut=boom)

    assert repo.count_reposted_videos_since(channel.id, _today()) == 1
    assert alerts == []


def test_expired_clip_does_not_alert(tmp_path):
    repo = _repo(tmp_path)
    channel = _kino_channel(repo, clips=1)
    now = datetime.datetime.utcnow()
    repo.create_clip_segment(
        channel_id=channel.id, video_ref="-223779047_10",
        start_seconds=0.0, end_seconds=35.0,
        clip_path=str(tmp_path / "missing.mp4"), text="Текст",
        scheduled_at=now - datetime.timedelta(hours=30),
    )
    alerts: list[tuple[str, str]] = []

    with patch("app.core.video.daily_video_repost.alert_once",
               side_effect=lambda repo_, text, key, **kw: alerts.append((key, text)) or True):
        publish_due_clips(repo, vk_publisher_for=lambda channel_: FakePublisher(), now=now)

    assert alerts == []


def test_film_alert_waits_a_full_day(tmp_path):
    """Фильм ровно один в сутки — вторая тревога за те же сутки была бы про ту же поломку."""
    from app.core.video.daily_video_repost import VIDEO_ALERT_COOLDOWN_HOURS

    assert VIDEO_ALERT_COOLDOWN_HOURS == 24


def test_unreadable_sources_alert_owner(tmp_path):
    """«Источники не читаются» и «новых видео нет» — разные вещи. Первое молчало вовсе."""
    repo = _repo(tmp_path)
    settings = ChannelSettings(
        daily_video_youtube_channels=["https://www.youtube.com/@kino"], daily_clip_count=0
    )
    channel = repo.create_channel(
        name="Кино", vk_destination="240120678", settings_json=settings.to_json()
    )
    alerts: list[tuple[str, str]] = []

    with patch("app.core.video.daily_video_repost.pick_unreposted_youtube",
               side_effect=RuntimeError("Sign in to confirm you're not a bot")), \
         patch("app.core.video.daily_video_repost.alert_once",
               side_effect=lambda repo_, text, key, **kw: alerts.append((key, text)) or True):
        run_daily_video_repost(
            repo, channel,
            vk_fetcher=None, vk_publisher=FakePublisher(),
            llm_client=None, footer_links=None, rng=random.Random(1),
        )

    assert len(alerts) == 1
    assert "источники не читаются" in alerts[0][1]
    assert repo.count_reposted_videos_since(channel.id, _today()) == 0


def test_exhausted_source_asks_for_a_new_one(tmp_path):
    """«Новых видео нет» больше не молчит: владелец должен узнать, что нужен источник."""
    repo = _repo(tmp_path)
    settings = ChannelSettings(
        daily_video_youtube_channels=["https://www.youtube.com/@kino"], daily_clip_count=0
    )
    channel = repo.create_channel(
        name="Кино", vk_destination="240120678", settings_json=settings.to_json()
    )
    alerts: list[tuple[str, str]] = []

    with patch("app.core.video.daily_video_repost.pick_unreposted_youtube", return_value=None), \
         patch("app.core.video.daily_video_repost.alert_once",
               side_effect=lambda repo_, text, key, **kw: alerts.append((key, text)) or True):
        run_daily_video_repost(
            repo, channel,
            vk_fetcher=None, vk_publisher=FakePublisher(),
            llm_client=None, footer_links=None, rng=random.Random(1),
        )

    assert len(alerts) == 1
    assert "Нужен новый источник" in alerts[0][1]


def test_channel_without_video_sources_stays_silent(tmp_path):
    """У канала без видео-источников фильмов и не ждут — тревожить не о чем."""
    repo = _repo(tmp_path)
    channel = repo.create_channel(
        name="Новости", vk_destination="233689032",
        settings_json=ChannelSettings().to_json(),
    )
    alerts: list[tuple[str, str]] = []

    with patch("app.core.video.daily_video_repost.alert_once",
               side_effect=lambda repo_, text, key, **kw: alerts.append((key, text)) or True):
        run_daily_video_repost(
            repo, channel,
            vk_fetcher=None, vk_publisher=FakePublisher(),
            llm_client=None, footer_links=None, rng=random.Random(1),
        )

    assert alerts == []


def test_lookback_window_is_passed_to_the_source(tmp_path):
    """Окно 20 было потолком всего софта: канал на тысячу фильмов выбирался за три недели."""
    repo = _repo(tmp_path)
    settings = ChannelSettings(
        daily_video_youtube_channels=["https://www.youtube.com/@kino"], daily_clip_count=0
    )
    channel = repo.create_channel(
        name="Кино", vk_destination="240120678", settings_json=settings.to_json()
    )

    with patch("app.core.video.daily_video_repost.pick_unreposted_youtube",
               return_value=None) as pick, \
         patch("app.core.video.daily_video_repost.alert_once", return_value=True):
        run_daily_video_repost(
            repo, channel,
            vk_fetcher=None, vk_publisher=FakePublisher(),
            llm_client=None, footer_links=None, rng=random.Random(1),
        )

    assert pick.call_args.kwargs["count"] == 200


def test_kino_diag_separates_taken_from_published(tmp_path):
    """/kino — SSH-свободный ответ на «почему нет фильмов»: видно взятые, но не вышедшие."""
    from app.control_bot import render_video_diag

    repo = _repo(tmp_path)
    channel = _kino_channel(repo)
    repo.add_reposted_video(channel_id=channel.id, video_ref="yt_ok", title="Вышедший")
    repo.mark_video_published(channel_id=channel.id, video_ref="yt_ok")
    repo.add_reposted_video(channel_id=channel.id, video_ref="yt_fail", title="Застрявший")

    text = render_video_diag(repo)

    assert "✅ вышел — Вышедший" in text
    assert "⛔ взят, но НЕ вышел — Застрявший" in text
    assert "ни один клип не ждёт публикации" in text


def test_alert_failure_does_not_break_the_job(tmp_path):
    """Тревога — способ УЗНАТЬ о поломке, а не ещё одна причина уронить джоб."""
    repo = _repo(tmp_path)
    channel = _kino_channel(repo)

    def boom(video, dest_dir, **kwargs):
        raise RuntimeError("сеть оборвалась")

    with patch("app.core.video.daily_video_repost.download_video", side_effect=boom), \
         patch("app.core.video.daily_video_repost.rewrite_video_texts",
               return_value=("Название", "Описание")), \
         patch("app.core.video.daily_video_repost.pick_unreposted_youtube", return_value=None), \
         patch("app.core.video.daily_video_repost.alert_once",
               side_effect=OSError("телеграм недоступен")):
        run_daily_video_repost(
            repo, channel,
            vk_fetcher=FakeFetcher([_vk_item(10)]), vk_publisher=FakePublisher(),
            llm_client=None, footer_links=None, rng=random.Random(1),
        )

    assert repo.count_reposted_videos_since(channel.id, _today()) == 0


def test_postponed_film_is_returned_to_the_queue(tmp_path):
    """Занятый VK-токен — не вина ролика. Фильм скачан зря, но не потерян."""
    repo = _repo(tmp_path)
    channel = _kino_channel(repo)
    publisher = FakePublisher(success=False, error="postponed: личный токен занят")

    alerts = _run(repo, channel, publisher, tmp_path)

    assert repo.list_reposted_video_refs(channel.id) == set()  # отметка снята
    assert repo.count_reposted_videos_since(channel.id, _today()) == 0
    assert alerts == []  # штатное состояние при одном аккаунте на четыре софта


def test_rejected_film_keeps_its_mark(tmp_path):
    """А вот отказ VK по содержимому отметку сохраняет — иначе качали бы по кругу."""
    repo = _repo(tmp_path)
    channel = _kino_channel(repo)

    alerts = _run(repo, channel, FakePublisher(success=False, error="[214] доступ закрыт"), tmp_path)

    assert repo.list_reposted_video_refs(channel.id) == {"-223779047_10"}
    assert len(alerts) == 1


def test_broken_video_does_not_cost_the_day(tmp_path):
    """Отказ бывает привязан к КОНКРЕТНОМУ ролику: 15.08 один отдавал 403 на данных,
    а соседние того же канала качались. Раньше это стоило суток без кино."""
    from app.core.video.video_source import SourceVideo

    repo = _repo(tmp_path)
    settings = ChannelSettings(
        daily_video_youtube_channels=["https://www.youtube.com/@kino"], daily_clip_count=0
    )
    channel = repo.create_channel(
        name="Кино", vk_destination="240120678", settings_json=settings.to_json()
    )
    candidates = iter([
        SourceVideo(ref="youtube_битый", title="Битый", description="", duration_seconds=3600,
                    direct_urls={}, page_url="https://youtube.com/watch?v=битый"),
        SourceVideo(ref="youtube_живой", title="Живой", description="", duration_seconds=3600,
                    direct_urls={}, page_url="https://youtube.com/watch?v=живой"),
    ])
    publisher = FakePublisher()

    def download(video, dest_dir, **kwargs):
        if video.ref == "youtube_битый":
            raise RuntimeError("HTTP Error 403: Forbidden")
        path = tmp_path / "film.mp4"
        path.write_bytes(b"video")
        return path

    alerts: list[tuple[str, str]] = []
    with patch("app.core.video.daily_video_repost.download_video", side_effect=download), \
         patch("app.core.video.daily_video_repost.cut_clips", return_value=[]), \
         patch("app.core.video.daily_video_repost.rewrite_video_texts",
               return_value=("Название", "Описание")), \
         patch("app.core.video.daily_video_repost.generate_clip_hooks", return_value=[]), \
         patch("app.core.video.daily_video_repost.pick_unreposted_youtube",
               side_effect=lambda *a, **kw: next(candidates, None)), \
         patch("app.core.video.daily_video_repost.alert_once",
               side_effect=lambda repo_, text, key, **kw: alerts.append((key, text)) or True):
        run_daily_video_repost(
            repo, channel, vk_fetcher=None, vk_publisher=publisher,
            llm_client=None, footer_links=None, rng=random.Random(1),
        )

    # день закрыт живым роликом, битый помечен и больше не выберется
    assert repo.count_reposted_videos_since(channel.id, _today()) == 1
    assert repo.list_reposted_video_refs(channel.id) == {"youtube_битый", "youtube_живой"}
    assert alerts == []
