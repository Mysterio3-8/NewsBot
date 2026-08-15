"""Добавление источника фильмов из бота.

Источники выгорают: каждая неудачная попытка помечает ролик взятым, и рано или поздно
софт пишет «все ролики источников уже публиковались». Раньше лечение было ручным —
правка сида, коммит, деплой; простой длился столько, сколько разработчик не за
клавиатурой. Теперь владелец отвечает на тревогу ссылкой.
"""
from app.core.channel_settings import ChannelSettings
from app.core.video.source_registry import add_video_source, normalize_source_url
from app.db.repository import Repository, init_db, make_engine


def _repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "sources.db")
    init_db(engine)
    return Repository(engine)


def _kino(repo, sources=("https://www.youtube.com/@old",)):
    settings = ChannelSettings(daily_video_youtube_channels=list(sources))
    return repo.create_channel(
        name="Кино", vk_destination="240120678", settings_json=settings.to_json()
    )


def test_share_link_is_cleaned_up():
    """Ссылка приходит из «поделиться» — с хвостом запроса и иногда с /videos."""
    assert normalize_source_url(
        "https://www.youtube.com/@KINO_PORT/videos?si=abc"
    ) == "https://www.youtube.com/@KINO_PORT"
    assert normalize_source_url(" https://youtube.com/channel/UCxxx/ ") == (
        "https://youtube.com/channel/UCxxx"
    )


def test_link_to_a_single_video_is_rejected():
    """Ошибка тихая и дорогая: ссылку на ролик софт примет, но список видео по ней
    всегда пуст — снаружи это «источник добавили, а фильмов нет»."""
    assert normalize_source_url("https://www.youtube.com/watch?v=abc123") is None
    assert normalize_source_url("просто текст") is None
    assert normalize_source_url("") is None


def test_source_is_added_to_the_video_channel(tmp_path):
    repo = _repo(tmp_path)
    channel = _kino(repo)

    answer = add_video_source(repo, "https://www.youtube.com/@KINO_PORT")

    assert "Источник добавлен" in answer
    settings = ChannelSettings.from_json(repo.get_channel(channel.id).settings_json)
    assert settings.daily_video_youtube_channels == [
        "https://www.youtube.com/@old",
        "https://www.youtube.com/@KINO_PORT",
    ]


def test_duplicate_is_reported_not_added_twice(tmp_path):
    repo = _repo(tmp_path)
    channel = _kino(repo)

    answer = add_video_source(repo, "https://www.youtube.com/@old")

    assert "уже добавлен" in answer
    settings = ChannelSettings.from_json(repo.get_channel(channel.id).settings_json)
    assert len(settings.daily_video_youtube_channels) == 1


def test_bad_link_explains_what_is_needed(tmp_path):
    repo = _repo(tmp_path)
    _kino(repo)

    answer = add_video_source(repo, "https://www.youtube.com/watch?v=abc")

    assert "не похоже на ссылку" in answer.lower()


def test_channel_without_video_is_not_touched(tmp_path):
    """У Новостей фильмов нет — источник видео им не нужен."""
    repo = _repo(tmp_path)
    repo.create_channel(
        name="Новости", vk_destination="233689032",
        settings_json=ChannelSettings().to_json(),
    )

    assert "не нашёл" in add_video_source(repo, "https://www.youtube.com/@KINO_PORT").lower()
