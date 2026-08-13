"""Регрессия: реальный data/app.db был создан до добавления content_hash
в RawPost, из-за чего get_recent_content_hashes падал с 'no such column'.
"""
import datetime

from sqlalchemy import create_engine, text

from app.db.repository import Repository, init_db, make_engine


def test_init_db_adds_missing_column_to_existing_table(tmp_path):
    db_path = tmp_path / "old_schema.db"
    old_engine = create_engine(f"sqlite:///{db_path}")
    with old_engine.connect() as connection:
        connection.execute(
            text(
                "CREATE TABLE raw_posts ("
                "id INTEGER PRIMARY KEY, source_id INTEGER, external_id VARCHAR(255), "
                "raw_text TEXT, media TEXT, fetched_at DATETIME)"
            )
        )
        connection.commit()
    old_engine.dispose()

    engine = make_engine(db_path)
    init_db(engine)  # не должно упасть и должно долить content_hash

    repo = Repository(engine)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    repo.create_raw_post(source_id=source.id, external_id="1", raw_text="текст", content_hash=42)

    assert repo.get_recent_content_hashes(source.id) == [42]


def test_init_db_adds_video_path_columns_to_existing_tables(tmp_path):
    db_path = tmp_path / "pre_video_schema.db"
    old_engine = create_engine(f"sqlite:///{db_path}")
    with old_engine.connect() as connection:
        connection.execute(
            text(
                "CREATE TABLE raw_posts ("
                "id INTEGER PRIMARY KEY, source_id INTEGER, external_id VARCHAR(255), "
                "raw_text TEXT, media TEXT, content_hash INTEGER, fetched_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE processed_posts ("
                "id INTEGER PRIMARY KEY, raw_post_id INTEGER, score FLOAT, category VARCHAR(100), "
                "rewritten_text TEXT, headline VARCHAR(500), image_paths TEXT, status VARCHAR(50), "
                "created_at DATETIME, published_at DATETIME)"
            )
        )
        connection.commit()
    old_engine.dispose()

    engine = make_engine(db_path)
    init_db(engine)  # не должно упасть и должно долить video_path в обе таблицы

    repo = Repository(engine)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    raw_post = repo.create_raw_post(
        source_id=source.id, external_id="1", raw_text="текст", video_path="raw/1.mp4"
    )
    processed = repo.create_processed_post(
        raw_post_id=raw_post.id, score=10.0, video_path="videos/1.mp4"
    )

    assert raw_post.video_path == "raw/1.mp4"
    assert processed.video_path == "videos/1.mp4"


def test_init_db_adds_per_network_published_columns_to_existing_table(tmp_path):
    """Регрессия: реальный проds app.db был создан до published_tg_at/published_vk_at
    (см. rate_guard.py фикс 2026-07-05) — без самолечащейся миграции mark_published
    падал бы с 'no such column' на существующей БД."""
    db_path = tmp_path / "pre_per_network_schema.db"
    old_engine = create_engine(f"sqlite:///{db_path}")
    with old_engine.connect() as connection:
        connection.execute(
            text(
                "CREATE TABLE raw_posts ("
                "id INTEGER PRIMARY KEY, source_id INTEGER, external_id VARCHAR(255), "
                "raw_text TEXT, media TEXT, content_hash INTEGER, fetched_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE processed_posts ("
                "id INTEGER PRIMARY KEY, raw_post_id INTEGER, score FLOAT, category VARCHAR(100), "
                "rewritten_text TEXT, headline VARCHAR(500), image_paths TEXT, status VARCHAR(50), "
                "created_at DATETIME, published_at DATETIME)"
            )
        )
        connection.commit()
    old_engine.dispose()

    engine = make_engine(db_path)
    init_db(engine)  # не должно упасть и должно долить published_tg_at/published_vk_at

    repo = Repository(engine)
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="текст")
    processed = repo.create_processed_post(raw_post_id=raw_post.id, score=10.0)

    import datetime

    repo.mark_published(processed.id, "tg", datetime.datetime.utcnow())

    assert repo.get_published_network_at(processed.id, "tg") is not None
    assert repo.get_published_network_at(processed.id, "vk") is None


def test_init_db_marks_old_reposted_videos_as_published(tmp_path):
    """Прод-БД жила без reposted_videos.published_at. Пустая колонка обнулила бы дневной
    счётчик фильмов задним числом — в день релиза Кино получило бы лишний фильм."""
    db_path = tmp_path / "pre_published_at.db"
    old_engine = create_engine(f"sqlite:///{db_path}")
    with old_engine.connect() as connection:
        connection.execute(
            text(
                "CREATE TABLE reposted_videos ("
                "id INTEGER PRIMARY KEY, channel_id INTEGER, video_ref VARCHAR(100), "
                "title TEXT, created_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO reposted_videos (channel_id, video_ref, title, created_at) "
                "VALUES (1, 'yt_old', 'Старый фильм', '2026-08-13 09:00:00')"
            )
        )
        connection.commit()
    old_engine.dispose()

    engine = make_engine(db_path)
    init_db(engine)

    repo = Repository(engine)
    channel = repo.create_channel(name="Кино", vk_destination="240120678")
    assert channel.id == 1
    since = datetime.datetime(2026, 8, 13)
    assert repo.count_reposted_videos_since(1, since) == 1

    # Повторный прогон ничего не переписывает: колонка уже есть, разметка одноразовая.
    repo.add_reposted_video(channel_id=1, video_ref="yt_new", title="Новый")
    init_db(engine)
    assert repo.count_reposted_videos_since(1, since) == 1


def test_init_db_is_idempotent_on_fresh_db(tmp_path):
    engine = make_engine(tmp_path / "fresh.db")
    init_db(engine)
    init_db(engine)  # повторный вызов не должен падать

    repo = Repository(engine)
    assert repo.list_sources() == []
