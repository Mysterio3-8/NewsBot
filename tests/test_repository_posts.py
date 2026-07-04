from app.db.repository import Repository, init_db, make_engine


def make_test_repository(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    return Repository(engine)


def test_create_raw_post_and_lookup_existing_ids(tmp_path):
    repo = make_test_repository(tmp_path)
    source = repo.create_source(type="tg", name="TG", url="https://t.me/x")

    repo.create_raw_post(source_id=source.id, external_id="100", raw_text="текст", content_hash=42)

    assert repo.get_existing_external_ids(source.id) == {"100"}
    assert repo.get_recent_content_hashes(source.id) == [42]


def test_create_raw_post_handles_content_hash_above_signed_64_boundary(tmp_path):
    repo = make_test_repository(tmp_path)
    source = repo.create_source(type="tg", name="TG", url="https://t.me/x")
    huge_unsigned_hash = (1 << 63) + 12345  # не влезает в знаковый INTEGER без конвертации

    repo.create_raw_post(
        source_id=source.id, external_id="1", raw_text="текст", content_hash=huge_unsigned_hash
    )

    assert repo.get_recent_content_hashes(source.id) == [huge_unsigned_hash]


def test_create_rejected_post(tmp_path):
    repo = make_test_repository(tmp_path)
    source = repo.create_source(type="tg", name="TG", url="https://t.me/x")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="реклама")

    rejected = repo.create_rejected_post(raw_post_id=raw_post.id, reason="реклама", score=10)

    assert rejected.reason == "реклама"
    assert rejected.score == 10


def test_create_processed_post_records_history(tmp_path):
    repo = make_test_repository(tmp_path)
    source = repo.create_source(type="tg", name="TG", url="https://t.me/x")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")

    processed = repo.create_processed_post(
        raw_post_id=raw_post.id, score=90.0, rewritten_text="переписанный текст", status="queued"
    )

    assert processed.status == "queued"
    queued = repo.list_processed_posts(status="queued")
    assert len(queued) == 1
    assert queued[0].id == processed.id


def test_update_processed_post_status_adds_history(tmp_path):
    repo = make_test_repository(tmp_path)
    source = repo.create_source(type="tg", name="TG", url="https://t.me/x")
    raw_post = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="новость")
    processed = repo.create_processed_post(raw_post_id=raw_post.id, score=90.0, status="queued")

    repo.update_processed_post_status(processed.id, "published")

    published = repo.list_processed_posts(status="published")
    assert len(published) == 1
    assert repo.list_processed_posts(status="queued") == []


def test_create_raw_post_and_processed_post_store_video_path(tmp_path):
    repo = make_test_repository(tmp_path)
    source = repo.create_source(type="tg", name="TG", url="https://t.me/x")

    raw_post = repo.create_raw_post(
        source_id=source.id, external_id="1", raw_text="видео-новость", video_path="raw/1/video.mp4"
    )
    processed = repo.create_processed_post(
        raw_post_id=raw_post.id, score=80.0, video_path="videos/1/video.mp4", status="queued"
    )

    assert raw_post.video_path == "raw/1/video.mp4"
    assert processed.video_path == "videos/1/video.mp4"


def test_list_recent_published_orders_by_published_at_descending(tmp_path):
    import datetime

    repo = make_test_repository(tmp_path)
    source = repo.create_source(type="tg", name="TG", url="https://t.me/x")
    raw_a = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="a")
    raw_b = repo.create_raw_post(source_id=source.id, external_id="2", raw_text="b")
    older = repo.create_processed_post(raw_post_id=raw_a.id, score=50.0, status="queued")
    newer = repo.create_processed_post(raw_post_id=raw_b.id, score=50.0, status="queued")
    repo.update_processed_post_status(
        older.id, "published", published_at=datetime.datetime(2026, 7, 1, 10, 0)
    )
    repo.update_processed_post_status(
        newer.id, "published", published_at=datetime.datetime(2026, 7, 3, 12, 40)
    )

    recent = repo.list_recent_published(limit=10)

    assert [p.id for p in recent] == [newer.id, older.id]


def test_list_recent_published_respects_limit(tmp_path):
    import datetime

    repo = make_test_repository(tmp_path)
    source = repo.create_source(type="tg", name="TG", url="https://t.me/x")
    for i in range(3):
        raw = repo.create_raw_post(source_id=source.id, external_id=str(i), raw_text="x")
        processed = repo.create_processed_post(raw_post_id=raw.id, score=50.0, status="queued")
        repo.update_processed_post_status(
            processed.id, "published", published_at=datetime.datetime(2026, 7, 1 + i, 0, 0)
        )

    assert len(repo.list_recent_published(limit=2)) == 2


def test_list_processed_posts_orders_by_score_descending(tmp_path):
    repo = make_test_repository(tmp_path)
    source = repo.create_source(type="tg", name="TG", url="https://t.me/x")
    raw_a = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="a")
    raw_b = repo.create_raw_post(source_id=source.id, external_id="2", raw_text="b")
    repo.create_processed_post(raw_post_id=raw_a.id, score=50.0, status="queued")
    repo.create_processed_post(raw_post_id=raw_b.id, score=90.0, status="queued")

    posts = repo.list_processed_posts(status="queued")

    assert [p.score for p in posts] == [90.0, 50.0]
