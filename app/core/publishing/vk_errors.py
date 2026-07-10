"""Классификация ошибок VK API для антибан-логики (circuit breaker).

VK на превышение частоты отвечает конкретными кодами (6/9/29) — на них нельзя
долбить ретраями, надо остановиться и подождать (пользовательское правило: пауза
10–15 мин). Код 5 = токен забанен/разлогинен (фатально). Всё прочее — транзиентно
(сеть, 5xx), можно ретраить. Классификация утиная (по `getattr`), чтобы не зависеть
от конкретного класса исключения vk_api и легко тестироваться подделками.
"""
from __future__ import annotations

import enum


class VKErrorClass(enum.Enum):
    RATE_LIMIT = "rate_limit"      # 6 (too many req/s), 9 (flood), 29 (rate limit), HTTP 429
    AUTH_BLOCKED = "auth_blocked"  # 5 — токен забанен/разлогинен, публиковать нельзя вовсе
    TRANSIENT = "transient"        # сеть, 5xx, 10 (internal) — можно повторить


_RATE_LIMIT_CODES = frozenset({6, 9, 29})
_AUTH_BLOCKED_CODES = frozenset({5})


def classify_vk_error(exc: BaseException) -> VKErrorClass:
    return classify_vk_code(_extract_code(exc), http_status=_extract_http_status(exc))


def classify_vk_code(code: int | None, *, http_status: int | None = None) -> VKErrorClass:
    if code in _RATE_LIMIT_CODES:
        return VKErrorClass.RATE_LIMIT
    if code in _AUTH_BLOCKED_CODES:
        return VKErrorClass.AUTH_BLOCKED
    if http_status == 429:
        return VKErrorClass.RATE_LIMIT
    return VKErrorClass.TRANSIENT


def _extract_code(exc: BaseException) -> int | None:
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


def _extract_http_status(exc: BaseException) -> int | None:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) else None
