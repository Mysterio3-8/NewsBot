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
    """Порог должен ловить поломку, а не случайно растянувшийся интервал.

    ⚠️ Считается от ОКНА софта, а не от суток (расписание по времени суток, 2026-08-14):
    Новости 3 поста за 9 ч, Кино 6 публикаций за 15 ч, Музыка 4 за 9 ч. Прежний счёт от
    суток занижал пороги и делал тревогу ежедневной."""
    plan = {  # имя: (публикаций в окне, длина окна в часах)
        "Новости": (3, 9),
        "Кино": (6, 15),
        "Infinity Music": (4, 9),
    }
    watched = {c.name: c for c in heartbeat.DEFAULT_WATCHLIST}

    for name, (posts, window) in plan.items():
        gap = window / posts
        assert watched[name].max_silence_hours >= gap * 2, name
        # И не настолько велик, чтобы поломка пряталась дольше целого окна.
        assert watched[name].max_silence_hours <= window, name


def test_night_silence_of_a_day_soft_is_not_an_alarm():
    """🔴 Кино работает 09:00–24:00 МСК. Ночью оно молчит ПО РАСПИСАНИЮ, и календарный
    счёт давал 9 часов тишины при пороге 8 — тревога уходила владельцу каждую ночь.
    Сторож, который кричит всегда, не замечают, когда он кричит по делу."""
    import datetime

    from app.core.maintenance.heartbeat import silence_hours

    # Последняя запись 20:42 МСК, сейчас 07:00 МСК следующего дня (UTC = МСК − 3).
    last = datetime.datetime(2026, 8, 16, 17, 42)
    now = datetime.datetime(2026, 8, 17, 4, 0)

    assert silence_hours(last, now, work_start=9, work_end=24) < 4
    assert silence_hours(last, now) > 10  # календарно — те самые «10 часов»


def test_working_hours_are_counted_inside_the_window():
    import datetime

    from app.core.maintenance.heartbeat import working_hours_between

    # 00:00–04:00 МСК целиком внутри окна Новостей.
    start = datetime.datetime(2026, 8, 16, 21, 0)  # 00:00 МСК
    end = datetime.datetime(2026, 8, 17, 1, 0)  # 04:00 МСК

    assert working_hours_between(start, end, 0, 9) == 4.0


def test_round_the_clock_window_equals_calendar_time():
    """Окно 0..24 обязано давать ровно прежнее поведение."""
    import datetime

    from app.core.maintenance.heartbeat import working_hours_between

    start = datetime.datetime(2026, 8, 16, 10, 0)
    end = datetime.datetime(2026, 8, 16, 15, 30)

    assert working_hours_between(start, end, 0, 24) == 5.5


def test_window_crossing_midnight_is_handled():
    """Окно Новостей 00:00–09:00 не пересекает полночь, а вот у Кино 09:00–24:00 конец
    упирается в неё — проверяем и обратный случай, чтобы формула не развалилась."""
    import datetime

    from app.core.maintenance.heartbeat import working_hours_between

    start = datetime.datetime(2026, 8, 16, 19, 0)  # 22:00 МСК
    end = datetime.datetime(2026, 8, 16, 22, 0)  # 01:00 МСК

    assert working_hours_between(start, end, 22, 2) == 3.0
