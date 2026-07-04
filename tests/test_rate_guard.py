import datetime

from app.core.publishing.rate_guard import check_publish_allowed
from app.db.repository import Repository, init_db, make_engine


def make_repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    return Repository(engine)


def _queued_post(repo: Repository, external_id: str = "1"):
    source = repo.create_source(type="tg", name="Канал", url="https://t.me/x")
    raw = repo.create_raw_post(source_id=source.id, external_id=external_id, raw_text="текст")
    return repo.create_processed_post(raw_post_id=raw.id, score=90, status="queued")


def test_allowed_when_no_prior_publications(tmp_path):
    repo = make_repo(tmp_path)
    post = _queued_post(repo)

    reason = check_publish_allowed(
        repo, post.id, max_posts_per_day=6, min_interval_minutes=180
    )
    assert reason is None


def test_blocked_when_daily_cap_reached(tmp_path):
    repo = make_repo(tmp_path)
    now = datetime.datetime.utcnow()
    # 6 уже опубликованных сегодня постов
    for i in range(6):
        p = _queued_post(repo, external_id=f"old{i}")
        repo.update_processed_post_status(p.id, "published", published_at=now)
    new_post = _queued_post(repo, external_id="new")

    reason = check_publish_allowed(
        repo, new_post.id, max_posts_per_day=6, min_interval_minutes=1, now=now
    )
    assert reason is not None
    assert "дневной лимит" in reason


def test_blocked_when_min_interval_not_elapsed(tmp_path):
    repo = make_repo(tmp_path)
    now = datetime.datetime.utcnow()
    recent = _queued_post(repo, external_id="recent")
    repo.update_processed_post_status(
        recent.id, "published", published_at=now - datetime.timedelta(minutes=30)
    )
    new_post = _queued_post(repo, external_id="new")

    reason = check_publish_allowed(
        repo, new_post.id, max_posts_per_day=6, min_interval_minutes=180, now=now
    )
    assert reason is not None
    assert "слишком рано" in reason


def test_allowed_when_min_interval_elapsed(tmp_path):
    repo = make_repo(tmp_path)
    now = datetime.datetime.utcnow()
    earlier = _queued_post(repo, external_id="earlier")
    repo.update_processed_post_status(
        earlier.id, "published", published_at=now - datetime.timedelta(minutes=200)
    )
    new_post = _queued_post(repo, external_id="new")

    reason = check_publish_allowed(
        repo, new_post.id, max_posts_per_day=6, min_interval_minutes=180, now=now
    )
    assert reason is None


def test_already_published_post_always_allowed_second_platform(tmp_path):
    """Тот же пост во вторую сеть (TG уже ушёл, теперь VK) — не новый релиз,
    стопор его не блокирует, даже если только что публиковали и лимит на грани."""
    repo = make_repo(tmp_path)
    now = datetime.datetime.utcnow()
    # Забиваем дневной лимит и ставим свежую последнюю публикацию
    for i in range(6):
        p = _queued_post(repo, external_id=f"old{i}")
        repo.update_processed_post_status(p.id, "published", published_at=now)

    # Наш пост уже опубликован (в TG), досылаем в VK
    same_post = _queued_post(repo, external_id="same")
    repo.update_processed_post_status(same_post.id, "published", published_at=now)

    reason = check_publish_allowed(
        repo, same_post.id, max_posts_per_day=6, min_interval_minutes=180, now=now
    )
    assert reason is None
