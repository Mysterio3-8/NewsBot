"""Раздельные лимиты по сетям: TG без ограничений, VK строго N в день.

ТЗ владельца 2026-08-20: «в тг новости без ограничений публиковались, а вк строго 3 в
день». Требование прямо противоречит жёсткой паре VK↔TG (ТЗ 2026-07-27 «один и тот же
пост в обе сети») — вместе они нереализуемы, поэтому пара для такого канала разрывается
осознанно, а у остальных каналов остаётся.
"""
import datetime

from app.core.channel_settings import ChannelSettings
from app.core.publishing.rate_guard import check_publish_allowed
from app.db.repository import Repository, init_db, make_engine


def _repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "split.db")
    init_db(engine)
    return Repository(engine)


def _post(repo: Repository, *, tg=None, vk=None) -> int:
    """Готовый пост с проставленными отметками публикации по сетям."""
    source = repo.create_source(type="telegram", name="s", url="u")
    raw = repo.create_raw_post(
        source_id=source.id, external_id=f"e{datetime.datetime.utcnow().timestamp()}",
        raw_text="текст", media=None,
    )
    post = repo.create_processed_post(raw_post_id=raw.id, rewritten_text="текст", score=80.0)
    if tg is not None:
        repo.mark_published(post.id, network="tg", published_at=tg)
    if vk is not None:
        repo.mark_published(post.id, network="vk", published_at=vk)
    return post.id


# --- настройки ----------------------------------------------------------------


def test_settings_survive_json_roundtrip():
    settings = ChannelSettings(tg_unlimited=True, vk_max_posts_per_day=3)

    restored = ChannelSettings.from_json(settings.to_json())

    assert restored.tg_unlimited is True
    assert restored.vk_max_posts_per_day == 3


def test_defaults_keep_the_old_behaviour():
    """Канал без флага (Кино) обязан работать ровно как раньше — жёсткой парой."""
    settings = ChannelSettings.from_json("{}")

    assert settings.tg_unlimited is False
    assert settings.vk_max_posts_per_day is None


# --- счётчик по сети ----------------------------------------------------------


def test_vk_counter_ignores_telegram_publications(tmp_path):
    """🔴 Главная ловушка режима: общий счётчик считает ВСЕ публикации канала, и три
    поста, ушедшие только в TG, закрыли бы VK на весь день, не выпустив там ни одного."""
    repo = _repo(tmp_path)
    since = datetime.datetime.utcnow() - datetime.timedelta(hours=1)
    now = datetime.datetime.utcnow()
    for _ in range(3):
        _post(repo, tg=now)
    _post(repo, tg=now, vk=now)

    assert repo.count_published_since(since, network="vk") == 1
    assert repo.count_published_since(since, network="tg") == 4


def test_vk_interval_is_measured_from_the_last_vk_post(tmp_path):
    """Интервал VK должен считаться от прошлой публикации В VK: поток TG иначе держал бы
    паузу VK открытой постоянно."""
    repo = _repo(tmp_path)
    long_ago = datetime.datetime.utcnow() - datetime.timedelta(hours=5)
    just_now = datetime.datetime.utcnow()
    _post(repo, vk=long_ago)
    _post(repo, tg=just_now)

    last_vk = repo.get_last_published_at(network="vk")

    assert last_vk is not None
    assert (datetime.datetime.utcnow() - last_vk).total_seconds() > 3600


# --- гейт ---------------------------------------------------------------------


def test_vk_cap_blocks_only_after_three_vk_publications(tmp_path):
    repo = _repo(tmp_path)
    now = datetime.datetime.utcnow()
    for _ in range(3):
        _post(repo, vk=now)
    candidate = _post(repo)

    blocked = check_publish_allowed(
        repo, candidate, network="vk", max_posts_per_day=3,
        min_interval_minutes=0, count_network="vk",
    )

    assert blocked is not None
    assert "лимит" in blocked


def test_three_telegram_posts_do_not_block_vk(tmp_path):
    repo = _repo(tmp_path)
    now = datetime.datetime.utcnow()
    for _ in range(3):
        _post(repo, tg=now)
    candidate = _post(repo)

    blocked = check_publish_allowed(
        repo, candidate, network="vk", max_posts_per_day=3,
        min_interval_minutes=0, count_network="vk",
    )

    assert blocked is None


def test_unknown_network_is_rejected_loudly(tmp_path):
    """Опечатка в имени сети не должна тихо считать «все публикации»."""
    import pytest

    repo = _repo(tmp_path)

    with pytest.raises(ValueError):
        repo.count_published_since(datetime.datetime.utcnow(), network="вк")
