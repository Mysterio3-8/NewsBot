"""Фильм и клипы двигают тот же интервальный гейт, что и текстовые посты."""
import datetime

from app.db.repository import Repository, init_db, make_engine


def _repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "app.db")
    init_db(engine)
    return Repository(engine)


def test_reposted_film_counts_as_channel_publication(tmp_path):
    """ТЗ владельца 2026-08-06: «кино так фильм, клип, пост, пост, пост, клип».
    Раньше три планировщика (фильм по своему окну, клипы по clip_segments, посты по
    очереди) не видели друг друга: get_last_published_at смотрел только на текстовые
    посты, поэтому пост мог выйти сразу за фильмом и порядок дня разъезжался."""
    repo = _repo(tmp_path)
    channel = repo.create_channel(name="Кино")

    assert repo.get_last_published_at(channel_id=channel.id) is None

    repo.add_reposted_video(channel_id=channel.id, video_ref="yt1", title="Фильм")

    last = repo.get_last_published_at(channel_id=channel.id)
    assert last is not None, "публикация фильма обязана двигать интервальный гейт"


def test_published_clip_counts_as_channel_publication(tmp_path):
    repo = _repo(tmp_path)
    channel = repo.create_channel(name="Кино")
    scheduled = datetime.datetime.utcnow()
    clip = repo.create_clip_segment(
        channel_id=channel.id,
        video_ref="yt1",
        start_seconds=0.0,
        end_seconds=35.0,
        clip_path="/tmp/c.mp4",
        text="t",
        scheduled_at=scheduled,
    )

    assert repo.get_last_published_at(channel_id=channel.id) is None  # ещё не вышел

    repo.mark_clip_published(clip.id)

    assert repo.get_last_published_at(channel_id=channel.id) is not None


def test_other_channel_activity_does_not_leak(tmp_path):
    """Гейт по каналу: фильм Кино не должен придерживать посты Новостей."""
    repo = _repo(tmp_path)
    kino = repo.create_channel(name="Кино")
    news = repo.create_channel(name="Новости")

    repo.add_reposted_video(channel_id=kino.id, video_ref="yt1", title="Фильм")

    assert repo.get_last_published_at(channel_id=news.id) is None
