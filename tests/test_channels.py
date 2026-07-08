"""Мультиканальность (срез 0a): модель Channel, привязка источников, миграция
существующих источников в «Канал 1 (Новости)»."""
from sqlalchemy import create_engine, text

from app.db.repository import DEFAULT_CHANNEL_NAME, Repository, init_db, make_engine


def test_create_and_list_channels(tmp_path):
    engine = make_engine(tmp_path / "channels.db")
    init_db(engine)
    repo = Repository(engine)

    kino = repo.create_channel(
        name="Кино",
        tg_destination="@my_kino",
        vk_token_env="VK_GROUP_TOKEN_KINO",
        vk_destination="12345",
    )

    channels = repo.list_channels()
    assert [c.name for c in channels] == ["Кино"]
    fetched = repo.get_channel(kino.id)
    assert fetched.tg_destination == "@my_kino"
    assert fetched.vk_token_env == "VK_GROUP_TOKEN_KINO"
    assert fetched.tg_token_env == "TG_BOT_TOKEN"  # дефолт


def test_list_channels_enabled_only_skips_disabled(tmp_path):
    engine = make_engine(tmp_path / "enabled.db")
    init_db(engine)
    repo = Repository(engine)

    on = repo.create_channel(name="Активный", enabled=True)
    repo.create_channel(name="Выключенный", enabled=False)

    assert [c.id for c in repo.list_channels(enabled_only=True)] == [on.id]


def test_source_belongs_to_channel(tmp_path):
    engine = make_engine(tmp_path / "src_channel.db")
    init_db(engine)
    repo = Repository(engine)

    kino = repo.create_channel(name="Кино")
    memes = repo.create_channel(name="Мемы")
    repo.create_source(type="vk", name="Кинопремьеры", url="111", channel_id=kino.id)
    repo.create_source(type="vk", name="bog_memes", url="222", channel_id=memes.id)

    kino_sources = repo.list_sources(channel_id=kino.id)
    assert [s.name for s in kino_sources] == ["Кинопремьеры"]
    assert repo.list_sources(channel_id=kino.id, source_type="vk")[0].url == "111"


def test_ensure_default_channel_migrates_orphan_sources(tmp_path):
    """Реальная прод-БД: таблица sources создана до мультиканальности (без channel_id).
    init_db должна долить колонку, завести «Новости» и привязать все источники к нему —
    прод продолжает работать."""
    db_path = tmp_path / "pre_channel_schema.db"
    old_engine = create_engine(f"sqlite:///{db_path}")
    with old_engine.connect() as connection:
        connection.execute(
            text(
                "CREATE TABLE sources ("
                "id INTEGER PRIMARY KEY, type VARCHAR(10), name VARCHAR(255), "
                "url VARCHAR(500), priority INTEGER, enabled BOOLEAN)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO sources (type, name, url, priority, enabled) "
                "VALUES ('tg', 'novosti_efir', 'https://t.me/novosti_efir', 5, 1)"
            )
        )
        connection.commit()
    old_engine.dispose()

    engine = make_engine(db_path)
    init_db(engine)

    repo = Repository(engine)
    channels = repo.list_channels()
    assert [c.name for c in channels] == [DEFAULT_CHANNEL_NAME]

    migrated = repo.list_sources()
    assert len(migrated) == 1
    assert migrated[0].channel_id == channels[0].id


def test_ensure_default_channel_is_idempotent(tmp_path):
    """Повторный init_db не должен плодить дубликаты «Новости» и переносить уже
    привязанные источники."""
    db_path = tmp_path / "idempotent.db"
    old_engine = create_engine(f"sqlite:///{db_path}")
    with old_engine.connect() as connection:
        connection.execute(
            text(
                "CREATE TABLE sources ("
                "id INTEGER PRIMARY KEY, type VARCHAR(10), name VARCHAR(255), "
                "url VARCHAR(500), priority INTEGER, enabled BOOLEAN)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO sources (type, name, url, priority, enabled) "
                "VALUES ('tg', 'novosti_efir', 'https://t.me/x', 5, 1)"
            )
        )
        connection.commit()
    old_engine.dispose()

    engine = make_engine(db_path)
    init_db(engine)
    init_db(engine)  # второй прогон

    repo = Repository(engine)
    assert len(repo.list_channels()) == 1


def test_fresh_db_has_no_channels_or_orphan_migration(tmp_path):
    """Чистая БД без источников — миграция не должна создавать пустой «Новости»."""
    engine = make_engine(tmp_path / "fresh_channels.db")
    init_db(engine)
    repo = Repository(engine)
    assert repo.list_channels() == []


def test_new_source_created_without_channel_stays_orphan_until_migration(tmp_path):
    """Источник, созданный без channel_id на уже мигрированной БД, не привязывается
    задним числом сам — привязка только через миграцию бесхозных при init_db."""
    engine = make_engine(tmp_path / "orphan_after.db")
    init_db(engine)
    repo = Repository(engine)

    src = repo.create_source(type="tg", name="X", url="https://t.me/x")
    assert src.channel_id is None
