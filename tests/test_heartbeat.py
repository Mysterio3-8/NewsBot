"""Сторож тишины: простой софта должен находиться сам, а не владельцем через сутки.

Все простои этой недели были зелёными по юнит-тестам — это пустые очереди и занятые
внешние ресурсы, а не ошибки в коде. Единственный надёжный признак — отсутствие записей
в сообществе.
"""
from __future__ import annotations

import datetime

from app.core.maintenance import heartbeat
from app.core.maintenance.heartbeat import (
    WatchedCommunity,
    build_silence_alert,
    find_silent_communities,
    last_post_moment,
    silence_hours,
)

NOW = datetime.datetime(2026, 8, 12, 12, 0)


def _item(hours_ago: float, **extra) -> dict:
    moment = NOW - datetime.timedelta(hours=hours_ago)
    return {"date": int(moment.replace(tzinfo=datetime.timezone.utc).timestamp()), **extra}


def test_latest_post_wins_over_pinned_one():
    """Закреплённая запись идёт в выдаче ПЕРВОЙ независимо от даты — брать первый
    элемент нельзя, иначе давний закреп маскировал бы мёртвый софт."""
    items = [_item(hours_ago=400, is_pinned=1), _item(hours_ago=2)]

    assert silence_hours(last_post_moment(items), NOW) == 2


def test_owner_reposts_do_not_count_as_life():
    """Владелец руками репостит анонсы розыгрышей сразу в несколько сообществ. Такой
    репост сделал бы стену «живой» при полностью вставшем автопостинге."""
    items = [_item(hours_ago=1, copy_history=[{"id": 1}]), _item(hours_ago=50)]

    assert silence_hours(last_post_moment(items), NOW) == 50


def test_empty_wall_is_infinite_silence():
    assert silence_hours(last_post_moment([]), NOW) == float("inf")


def test_community_over_threshold_is_reported(monkeypatch):
    monkeypatch.setattr(heartbeat, "fetch_wall_items", lambda token, gid, count=10: [_item(20)])
    watchlist = (WatchedCommunity("Кино", 1, max_silence_hours=8),)

    stale = find_silent_communities("token", watchlist, now=NOW)

    assert [c.name for c, _ in stale] == ["Кино"]


def test_community_within_threshold_is_quiet(monkeypatch):
    monkeypatch.setattr(heartbeat, "fetch_wall_items", lambda token, gid, count=10: [_item(3)])
    watchlist = (WatchedCommunity("Новости", 1, max_silence_hours=6),)

    assert find_silent_communities("token", watchlist, now=NOW) == []


def test_unreadable_wall_never_triggers_an_alert(monkeypatch):
    """Молчание VK про наши записи и молчание софта — разные вещи. Путать их значит
    слать ложную тревогу при каждом сбое сети."""
    monkeypatch.setattr(heartbeat, "fetch_wall_items", lambda token, gid, count=10: [])
    watchlist = (WatchedCommunity("Минусы", 1, max_silence_hours=30),)

    assert find_silent_communities("token", watchlist, now=NOW) == []


def test_alert_names_the_soft_and_its_threshold():
    text = build_silence_alert([(WatchedCommunity("Кино", 1, max_silence_hours=8), 20.0)])

    assert "Кино" in text
    assert "20 ч" in text
    assert "норма до 8 ч" in text


def test_thresholds_are_at_least_double_the_publishing_rate():
    """Порог должен ловить поломку, а не случайно растянувшийся интервал. Объёмы —
    в all_auto/CLAUDE.md: Новости 10/сутки, Кино 7, Музыка 4, Минусы 1."""
    expected_gap_hours = {"Новости": 24 / 10, "Кино": 24 / 7, "Infinity Music": 24 / 4}
    watched = {c.name: c for c in heartbeat.DEFAULT_WATCHLIST}

    for name, gap in expected_gap_hours.items():
        assert watched[name].max_silence_hours >= gap * 2, name
