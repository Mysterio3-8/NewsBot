"""Технический лимитер частоты запросов на токен (не бизнес-лимит постов/день).

VK: не более 2 запросов/сек на один токен (пользовательское правило) — иначе
ошибка 6 «too many requests per second». Держит минимальный интервал между
вызовами одного токена, in-process (один долгоживущий headless-процесс). Отличается
от rate_guard (тот — сколько ПОСТОВ в сутки; этот — сколько API-ЗАПРОСОВ в секунду).
"""
from __future__ import annotations

import hashlib
import time


def token_key(secret: str) -> str:
    """Стабильный нечувствительный к содержимому ключ для TokenBucket — сам токен
    не должен оседать как строка-ключ в долгоживущей структуре процесса."""
    return hashlib.sha256(secret.encode()).hexdigest()[:16]


class TokenBucket:
    def __init__(
        self,
        max_requests_per_second: float = 2.0,
        *,
        clock=time.monotonic,
        sleep=time.sleep,
    ) -> None:
        if max_requests_per_second <= 0:
            raise ValueError("max_requests_per_second должно быть > 0")
        self._min_interval = 1.0 / max_requests_per_second
        self._clock = clock
        self._sleep = sleep
        self._last_call: dict[str, float] = {}

    def wait(self, token_key: str) -> None:
        """Блокирует ровно настолько, чтобы выдержать минимальный интервал с прошлого
        вызова того же токена. Разные токены не мешают друг другу."""
        last = self._last_call.get(token_key)
        if last is not None:
            elapsed = self._clock() - last
            remaining = self._min_interval - elapsed
            if remaining > 0:
                self._sleep(remaining)
        self._last_call[token_key] = self._clock()
