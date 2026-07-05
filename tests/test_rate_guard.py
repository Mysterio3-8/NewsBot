import datetime

import pytest

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
        repo, post.id, network="tg", max_posts_per_day=6, min_interval_minutes=180
    )
    assert reason is None


def test_rejects_unknown_network(tmp_path):
    repo = make_repo(tmp_path)
    post = _queued_post(repo)
    with pytest.raises(ValueError):
        check_publish_allowed(
            repo, post.id, network="telegram", max_posts_per_day=6, min_interval_minutes=180
        )


def test_blocked_when_daily_cap_reached(tmp_path):
    repo = make_repo(tmp_path)
    now = datetime.datetime.utcnow()
    for i in range(6):
        p = _queued_post(repo, external_id=f"old{i}")
        repo.mark_published(p.id, "tg", now)
    new_post = _queued_post(repo, external_id="new")

    reason = check_publish_allowed(
        repo, new_post.id, network="tg", max_posts_per_day=6, min_interval_minutes=1, now=now
    )
    assert reason is not None
    assert "дневной лимит" in reason


def test_blocked_when_min_interval_not_elapsed(tmp_path):
    repo = make_repo(tmp_path)
    now = datetime.datetime.utcnow()
    recent = _queued_post(repo, external_id="recent")
    repo.mark_published(recent.id, "tg", now - datetime.timedelta(minutes=30))
    new_post = _queued_post(repo, external_id="new")

    reason = check_publish_allowed(
        repo, new_post.id, network="tg", max_posts_per_day=6, min_interval_minutes=180, now=now
    )
    assert reason is not None
    assert "слишком рано" in reason


def test_allowed_when_min_interval_elapsed(tmp_path):
    repo = make_repo(tmp_path)
    now = datetime.datetime.utcnow()
    earlier = _queued_post(repo, external_id="earlier")
    repo.mark_published(earlier.id, "tg", now - datetime.timedelta(minutes=200))
    new_post = _queued_post(repo, external_id="new")

    reason = check_publish_allowed(
        repo, new_post.id, network="tg", max_posts_per_day=6, min_interval_minutes=180, now=now
    )
    assert reason is None


def test_already_published_post_allowed_on_the_other_network(tmp_path):
    """Тот же пост во вторую сеть (TG уже ушёл, теперь VK) — не новый релиз,
    стопор его не блокирует, даже если только что публиковали и лимит на грани."""
    repo = make_repo(tmp_path)
    now = datetime.datetime.utcnow()
    for i in range(6):
        p = _queued_post(repo, external_id=f"old{i}")
        repo.mark_published(p.id, "tg", now)

    same_post = _queued_post(repo, external_id="same")
    repo.mark_published(same_post.id, "tg", now)

    reason = check_publish_allowed(
        repo, same_post.id, network="vk", max_posts_per_day=6, min_interval_minutes=180, now=now
    )
    assert reason is None


def test_blocked_when_already_published_to_same_network(tmp_path):
    """КРИТИЧНАЯ регрессия (2026-07-05): пост, уже опубликованный в TG, второй раз
    в ТУ ЖЕ сеть (TG) должен блокироваться всегда — независимо от дневного лимита
    и интервала. На проде это привело к 28 повторным публикациям одного поста в
    один и тот же TG-канал за 5 часов, потому что старая проверка смотрела только
    на общий status=='published', не на конкретную сеть."""
    repo = make_repo(tmp_path)
    now = datetime.datetime.utcnow()
    post = _queued_post(repo, external_id="same")
    repo.mark_published(post.id, "tg", now - datetime.timedelta(minutes=500))

    reason = check_publish_allowed(
        repo, post.id, network="tg", max_posts_per_day=6, min_interval_minutes=1, now=now
    )
    assert reason is not None
    assert "уже опубликован в tg" in reason


def test_blocked_when_already_published_to_same_network_vk(tmp_path):
    repo = make_repo(tmp_path)
    now = datetime.datetime.utcnow()
    post = _queued_post(repo, external_id="same")
    repo.mark_published(post.id, "vk", now - datetime.timedelta(minutes=500))

    reason = check_publish_allowed(
        repo, post.id, network="vk", max_posts_per_day=6, min_interval_minutes=1, now=now
    )
    assert reason is not None
    assert "уже опубликован в vk" in reason
