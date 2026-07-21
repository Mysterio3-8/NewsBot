import datetime

import pytest

from app.core.publishing.rate_guard import check_publish_allowed, required_interval_minutes
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


def test_per_channel_limit_isolates_channels(tmp_path):
    """Защита от бана + изоляция каналов: дневной лимит считается по публикациям
    КОНКРЕТНОГО канала — публикации другого канала на него не влияют."""
    repo = make_repo(tmp_path)
    now = datetime.datetime.utcnow()
    ch_a = repo.create_channel(name="A")
    ch_b = repo.create_channel(name="B")
    src_a = repo.create_source(type="vk", name="sa", url="-1", channel_id=ch_a.id)
    src_b = repo.create_source(type="vk", name="sb", url="-2", channel_id=ch_b.id)

    for i in range(4):  # канал A выложил 4 поста
        raw = repo.create_raw_post(source_id=src_a.id, external_id=f"a{i}", raw_text="t")
        p = repo.create_processed_post(raw_post_id=raw.id, score=90, status="queued")
        repo.mark_published(p.id, "vk", now)

    # у канала B 0 публикаций → его лимит 4 не тронут, несмотря на 4 поста канала A
    raw_b = repo.create_raw_post(source_id=src_b.id, external_id="b1", raw_text="t")
    post_b = repo.create_processed_post(raw_post_id=raw_b.id, score=90, status="queued")
    assert check_publish_allowed(
        repo, post_b.id, network="vk", max_posts_per_day=4,
        min_interval_minutes=0, channel_id=ch_b.id, now=now,
    ) is None

    # у канала A уже 4 → его лимит достигнут
    raw_a = repo.create_raw_post(source_id=src_a.id, external_id="a_new", raw_text="t")
    post_a = repo.create_processed_post(raw_post_id=raw_a.id, score=90, status="queued")
    reason = check_publish_allowed(
        repo, post_a.id, network="vk", max_posts_per_day=4,
        min_interval_minutes=0, channel_id=ch_a.id, now=now,
    )
    assert reason is not None and "дневной лимит" in reason


def test_required_interval_is_min_without_upper_bound():
    last = datetime.datetime(2026, 7, 21, 10, 0)

    assert required_interval_minutes(90, None, last) == 90


def test_required_interval_is_random_within_range():
    """ТЗ 2026-07-21: пауза 1.5-4 часа на рандом."""
    values = {
        required_interval_minutes(90, 240, datetime.datetime(2026, 7, 21, 10, minute))
        for minute in range(0, 60)
    }

    assert all(90 <= value <= 240 for value in values)
    assert len(values) > 1  # интервал реально меняется от публикации к публикации


def test_required_interval_is_stable_for_the_same_gap():
    """Иначе интервал пере-бросался бы на каждой проверке очереди, и пост уходил бы
    по первому удачному броску — то есть всегда по нижней границе."""
    last = datetime.datetime(2026, 7, 21, 10, 0)

    assert required_interval_minutes(90, 240, last) == required_interval_minutes(90, 240, last)
