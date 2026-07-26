"""Сид канала «Новости» (ТЗ 2026-07-26): включить VK+TG, оставить один источник
«прямой эфир», выставить консервативный антибан. Идемпотентно."""
from app.core.channel_settings import ChannelSettings
from app.db.repository import DEFAULT_CHANNEL_NAME, Repository, init_db, make_engine
from app.seed_channels import NEWS_SOURCE_URL, seed_news


def _news_channel(repo: Repository):
    return next(c for c in repo.list_channels() if c.name == DEFAULT_CHANNEL_NAME)


def _seed_default_channel(repo: Repository, *, enabled: bool = True):
    """На проде канал «Новости» уже существует (создан миграцией _ensure_default_channel).
    В пустой тест-БД его нет — заводим сами, чтобы моделировать прод."""
    return repo.create_channel(name=DEFAULT_CHANNEL_NAME, enabled=enabled)


def test_seed_news_enables_channel_and_narrows_to_single_source(tmp_path):
    engine = make_engine(tmp_path / "news.db")
    init_db(engine)
    repo = Repository(engine)
    channel = _seed_default_channel(repo, enabled=False)
    repo.create_source(type="tg", name="прямой эфир", url=NEWS_SOURCE_URL, channel_id=channel.id)
    repo.create_source(type="tg", name="лишний", url="https://t.me/toporlive", channel_id=channel.id)
    repo.create_source(type="vk", name="лишний-vk", url="-191269950", channel_id=channel.id)

    seed_news(repo)

    channel = _news_channel(repo)
    assert channel.enabled is True
    enabled_urls = {s.url for s in repo.list_sources(channel_id=channel.id) if s.enabled}
    assert enabled_urls == {NEWS_SOURCE_URL}


def test_seed_news_sets_conservative_antiban(tmp_path):
    engine = make_engine(tmp_path / "antiban.db")
    init_db(engine)
    repo = Repository(engine)
    _seed_default_channel(repo)

    seed_news(repo)

    settings = ChannelSettings.from_json(_news_channel(repo).settings_json)
    assert settings.max_posts_per_day <= 50
    assert settings.min_interval_minutes is not None
    assert settings.max_interval_minutes is not None
    assert settings.min_interval_minutes < settings.max_interval_minutes
    assert settings.quiet_start_hour is not None and settings.quiet_end_hour is not None


def test_seed_news_is_idempotent(tmp_path):
    engine = make_engine(tmp_path / "idem.db")
    init_db(engine)
    repo = Repository(engine)
    _seed_default_channel(repo)

    seed_news(repo)
    seed_news(repo)

    channel = _news_channel(repo)
    assert channel.enabled is True
    # settings_json не раздувается дублями ключей — merge обновляет на месте
    assert ChannelSettings.from_json(channel.settings_json).max_posts_per_day == 20
