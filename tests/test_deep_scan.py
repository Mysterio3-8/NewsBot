"""Обход источника вглубь, когда свежих постов не осталось (ТЗ владельца 2026-08-30).

«Если все посты закончатся, надо заново по кругу… можно и старые, можно максимально
даже вниз спускаться».
"""
import datetime
from types import SimpleNamespace
from unittest.mock import Mock

from app.core.channel_settings import ChannelSettings
from app.core.check_cycle import (
    DEEP_SCAN_PAGE,
    _channel_queue_is_empty,
    _fetch_vk_deeper,
    _vk_deep_offset_key,
)
from app.core.monitoring.models import FetchedPost
from app.core.monitoring.vk_fetcher import WallPage
from app.db.repository import Repository, init_db, make_engine


def make_repo(tmp_path) -> Repository:
    engine = make_engine(tmp_path / "test.db")
    init_db(engine)
    return Repository(engine)


def make_source(repo, *, channel_id=None):
    return repo.create_source(
        type="vk", name="Кинопремьеры", url="58170807", channel_id=channel_id
    )


def make_post(external_id: str) -> FetchedPost:
    return FetchedPost(
        external_id=external_id,
        text="Старый пост про фильм",
        post_type="text",
        views=100,
        published_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        has_media=False,
    )


def test_deep_scan_advances_offset_between_passes(tmp_path):
    """Второй заход начинается там, где кончился первый, а не с начала стены."""
    repo = make_repo(tmp_path)
    source = make_source(repo)
    fetcher = Mock()
    fetcher.fetch_wall_page.return_value = WallPage(posts=[make_post("100")], items_seen=10)

    _fetch_vk_deeper(repo, fetcher, source)
    assert fetcher.fetch_wall_page.call_args.kwargs["offset"] == 0

    _fetch_vk_deeper(repo, fetcher, source)
    assert fetcher.fetch_wall_page.call_args.kwargs["offset"] == 10
    assert fetcher.fetch_wall_page.call_args.kwargs["count"] == DEEP_SCAN_PAGE


def test_offset_resets_at_the_end_of_the_wall(tmp_path):
    """Стена кончилась → смещение обнуляется, обход идёт на новый круг."""
    repo = make_repo(tmp_path)
    source = make_source(repo)
    repo.set_setting(_vk_deep_offset_key(source.id), "500")
    fetcher = Mock()
    fetcher.fetch_wall_page.return_value = WallPage(posts=[], items_seen=0)

    assert _fetch_vk_deeper(repo, fetcher, source) == []
    assert repo.get_setting(_vk_deep_offset_key(source.id)) == "0"


def test_page_without_new_posts_still_moves_deeper(tmp_path):
    """Все записи страницы уже известны — смещение всё равно растёт, иначе обход
    навсегда застрял бы на одном и том же месте."""
    repo = make_repo(tmp_path)
    source = make_source(repo)
    fetcher = Mock()
    fetcher.fetch_wall_page.return_value = WallPage(posts=[], items_seen=10)

    assert _fetch_vk_deeper(repo, fetcher, source) == []
    assert repo.get_setting(_vk_deep_offset_key(source.id)) == "10"


def test_broken_source_does_not_raise(tmp_path):
    """Сломанный источник не должен рушить цикл проверки — как и в обычном фетче."""
    repo = make_repo(tmp_path)
    source = make_source(repo)
    fetcher = Mock()
    fetcher.fetch_wall_page.side_effect = RuntimeError("VK недоступен")

    assert _fetch_vk_deeper(repo, fetcher, source) == []


def config_with_freshness(hours: int = 6):
    return SimpleNamespace(
        publishing=SimpleNamespace(schedule=SimpleNamespace(publish_freshness_hours=hours))
    )


def test_queue_with_posts_blocks_deep_scan(tmp_path):
    """Пока в очереди канала есть что публиковать, в архив не лезем: каждый взятый
    оттуда пост стоит вызовов LLM."""
    repo = make_repo(tmp_path)
    channel = repo.create_channel(name="Кино")
    source = make_source(repo, channel_id=channel.id)
    raw = repo.create_raw_post(source_id=source.id, external_id="1", raw_text="текст")
    repo.create_processed_post(
        raw_post_id=raw.id, score=90.0, category="кино",
        rewritten_text="Рерайт", headline="Хук", status="queued",
    )

    assert _channel_queue_is_empty(repo, source, config_with_freshness()) is False


def test_empty_queue_allows_deep_scan(tmp_path):
    repo = make_repo(tmp_path)
    channel = repo.create_channel(name="Кино")
    source = make_source(repo, channel_id=channel.id)

    assert _channel_queue_is_empty(repo, source, config_with_freshness()) is True


def test_source_without_channel_never_deep_scans(tmp_path):
    repo = make_repo(tmp_path)
    source = make_source(repo)

    assert _channel_queue_is_empty(repo, source, config_with_freshness()) is False


def test_setting_survives_json_roundtrip():
    assert ChannelSettings.from_json(ChannelSettings(deep_scan=True).to_json()).deep_scan is True
    assert ChannelSettings.from_json("{}").deep_scan is False
