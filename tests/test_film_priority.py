"""Фильм получает слот загрузки раньше текстовых постов.

ТЗ владельца 2026-08-14: «самое главное, чтобы были фильмы». После починки YouTube
главной угрозой стала не сеть, а очередь: единственный личный VK-аккаунт делят четыре
софта, зазор между загрузками 60 минут, слот достаётся тому, кто попросил первым.
Постов много и они идут весь день, фильм один — в честной гонке он проигрывал (14.08
проиграл трижды подряд, и сутки снова остались без кино).
"""
import datetime

from app.core.channel_settings import ChannelSettings
from app.headless_service import FILM_PRIORITY_HOURS, film_has_priority
from app.db.repository import Repository, init_db, make_engine


def _repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "priority.db")
    init_db(engine)
    return Repository(engine)


def _kino(repo, **overrides):
    settings = ChannelSettings(
        daily_video_youtube_channels=["https://www.youtube.com/@kino"],
        daily_video_count=1,
        daily_video_start_hour_utc=8,
        **overrides,
    )
    return repo.create_channel(
        name="Кино", vk_destination="240120678", settings_json=settings.to_json()
    ), settings


def test_film_wins_the_slot_until_it_is_published(tmp_path):
    repo = _repo(tmp_path)
    channel, settings = _kino(repo)
    inside_window = datetime.datetime(2026, 8, 14, 9, 0)

    assert film_has_priority(repo, channel, settings, inside_window) is True

    repo.add_reposted_video(channel_id=channel.id, video_ref="yt_1", title="Фильм")
    repo.mark_video_published(channel_id=channel.id, video_ref="yt_1")

    assert film_has_priority(repo, channel, settings, inside_window) is False


def test_priority_expires_so_the_channel_never_goes_fully_silent(tmp_path):
    """Бессрочный приоритет был бы хуже болезни: сломайся фильм — замолчал бы весь канал."""
    repo = _repo(tmp_path)
    channel, settings = _kino(repo)
    too_late = datetime.datetime(2026, 8, 14, 8 + FILM_PRIORITY_HOURS + 1, 0)

    assert film_has_priority(repo, channel, settings, too_late) is False


def test_before_the_window_posts_go_as_usual(tmp_path):
    repo = _repo(tmp_path)
    channel, settings = _kino(repo)

    assert film_has_priority(repo, channel, settings, datetime.datetime(2026, 8, 14, 7, 0)) is False


def test_channel_without_films_is_untouched(tmp_path):
    """У Новостей фильмов нет — их посты уступать никому не должны."""
    repo = _repo(tmp_path)
    settings = ChannelSettings()
    channel = repo.create_channel(
        name="Новости", vk_destination="233689032", settings_json=settings.to_json()
    )

    assert film_has_priority(repo, channel, settings, datetime.datetime(2026, 8, 14, 9, 0)) is False
