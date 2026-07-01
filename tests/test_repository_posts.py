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


def test_list_processed_posts_orders_by_score_descending(tmp_path):
    repo = make_test_repository(tmp_path)
    source = repo.create_source(type="tg", name="TG", url="https://t.me/x")
    raw_a = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="a")
    raw_b = repo.create_raw_post(source_id=source.id, external_id="2", raw_text="b")
    repo.create_processed_post(raw_post_id=raw_a.id, score=50.0, status="queued")
    repo.create_processed_post(raw_post_id=raw_b.id, score=90.0, status="queued")

    posts = repo.list_processed_posts(status="queued")

    assert [p.score for p in posts] == [90.0, 50.0]
