"""Читатель VK переходит на запасной токен, когда основной отозвали.

Живой случай 2026-09-08: `VK_USER_TOKEN` отозвали посреди суток (вход в кабинет VK
Бизнес ID завершает сторонние сессии), и источники Кино перестали читаться совсем —
`[5] User authorization failed`. Один токен = одна точка отказа на весь мониторинг.
"""

from __future__ import annotations

import pytest
import vk_api

from app.core.monitoring.vk_fetcher import VKFetcher


class _Method:
    """Заглушка ветки `api.wall.get`: считает вызовы и отдаёт заданный исход."""

    def __init__(self, outcomes: list):
        self.outcomes = outcomes
        self.calls = 0

    def __call__(self, **kwargs):
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _Api:
    def __init__(self, method: _Method):
        self.wall = type("Wall", (), {"get": method})()


def _auth_error() -> vk_api.exceptions.ApiError:
    return vk_api.exceptions.ApiError(
        vk_api.VkApi(token="x"),
        "wall.get",
        {},
        {},
        {"error_code": 5, "error_msg": "User authorization failed: invalid access_token (4)."},
    )


def _fetcher(apis: list[_Api], tokens: int = 2) -> VKFetcher:
    """Собираем читатель без сети: __init__ ходит в vk_api, поэтому обходим его."""
    fetcher = VKFetcher.__new__(VKFetcher)
    fetcher._tokens = [f"token{i}" for i in range(tokens)]
    fetcher._token_index = 0
    fetcher._apis = apis
    fetcher._api = apis[0]
    return fetcher


def test_switches_to_the_spare_token_on_authorization_failure(monkeypatch):
    dead = _Method([_auth_error()])
    alive = _Method([{"items": [], "count": 0}])
    fetcher = _fetcher([_Api(dead), _Api(alive)])

    def switch() -> bool:
        if fetcher._token_index + 1 >= len(fetcher._tokens):
            return False
        fetcher._token_index += 1
        fetcher._api = fetcher._apis[fetcher._token_index]
        return True

    monkeypatch.setattr(fetcher, "_switch_token", switch)

    result = fetcher._call("wall.get", owner_id=-1, count=10)

    assert result == {"items": [], "count": 0}
    assert dead.calls == 1, "мёртвый токен дёргаем ровно один раз"
    assert alive.calls == 1


def test_other_errors_are_not_retried_on_another_token(monkeypatch):
    """Нет прав или закрытая стена — на другом токене будет ровно то же.

    Перебор здесь только удвоил бы число обращений с личных аккаунтов, то есть ровно
    ту активность, из-за которой VK их и банит."""
    error = vk_api.exceptions.ApiError(
        vk_api.VkApi(token="x"), "wall.get", {}, {},
        {"error_code": 15, "error_msg": "Access denied"},
    )
    first = _Method([error])
    second = _Method([{"items": []}])
    fetcher = _fetcher([_Api(first), _Api(second)])
    monkeypatch.setattr(fetcher, "_switch_token", lambda: pytest.fail("не должен переключаться"))

    with pytest.raises(vk_api.exceptions.ApiError):
        fetcher._call("wall.get", owner_id=-1, count=10)

    assert second.calls == 0


def test_last_token_failure_propagates():
    """Запасных не осталось — ошибка идёт наверх, а не глотается: без неё мониторинг
    молча считал бы, что источник пуст."""
    dead = _Method([_auth_error()])
    fetcher = _fetcher([_Api(dead)], tokens=1)

    with pytest.raises(vk_api.exceptions.ApiError):
        fetcher._call("wall.get", owner_id=-1, count=10)
