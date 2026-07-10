"""Circuit breaker публикации: при серии ошибок сети «размыкает цепь» и на время
запрещает публиковать в эту сеть, вместо того чтобы долбить API и углублять бан.

Пользовательское правило (VK): ошибка 6/9/29 (или HTTP 429) → пауза 10–15 мин.
Код 5 (токен забанен) → длинная пауза 60 мин + громкий лог. Прочие ошибки подряд
(≥ threshold) → тоже пауза, чтобы не молотить в упавшую сеть.

Состояние персистится в `settings` (переживает рестарт сервиса — важно, т.к.
AUTOSTART_SERVICE запускает цикл сразу при рестарте и иначе обошёл бы кулдаун).
Ключ — `breaker:{network}:{token_env}`: имя env-переменной, НЕ сам секрет.
"""
from __future__ import annotations

import datetime
import json
import logging
import random
from dataclasses import dataclass

from app.core.publishing.vk_errors import VKErrorClass
from app.db.repository import Repository

logger = logging.getLogger("publishing")


@dataclass(frozen=True)
class BreakerConfig:
    rate_limit_cooldown_minutes: tuple[int, int] = (10, 15)  # рандом внутри диапазона
    auth_blocked_cooldown_minutes: int = 60
    failure_threshold: int = 3  # столько прочих ошибок подряд → пауза


class CircuitBreaker:
    def __init__(
        self,
        repo: Repository,
        config: BreakerConfig | None = None,
        *,
        rng=random.uniform,
    ) -> None:
        self._repo = repo
        self._config = config or BreakerConfig()
        self._rng = rng

    def is_open(
        self, network: str, token_env: str, *, now: datetime.datetime | None = None
    ) -> bool:
        state = self._load(network, token_env)
        open_until = state.get("open_until")
        if not open_until:
            return False
        now = now or datetime.datetime.utcnow()
        return now < datetime.datetime.fromisoformat(open_until)

    def record_success(self, network: str, token_env: str) -> None:
        self._save(network, token_env, {"fails": 0, "open_until": None})

    def record_failure(
        self,
        network: str,
        token_env: str,
        error_class: VKErrorClass,
        *,
        now: datetime.datetime | None = None,
    ) -> None:
        now = now or datetime.datetime.utcnow()
        state = self._load(network, token_env)

        if error_class is VKErrorClass.RATE_LIMIT:
            self._open(network, token_env, now, self._rate_limit_cooldown(), fails=0)
            logger.warning(
                "Circuit breaker открыт для %s (%s): rate limit, пауза до %s",
                network, token_env, self._rate_limit_cooldown_note(now),
            )
            return

        if error_class is VKErrorClass.AUTH_BLOCKED:
            minutes = self._config.auth_blocked_cooldown_minutes
            self._open(network, token_env, now, minutes, fails=0)
            logger.error(
                "Circuit breaker открыт для %s (%s): токен ЗАБАНЕН/разлогинен — "
                "публикация приостановлена на %d мин, проверь токен",
                network, token_env, minutes,
            )
            return

        fails = state.get("fails", 0) + 1
        if fails >= self._config.failure_threshold:
            self._open(network, token_env, now, self._rate_limit_cooldown(), fails=0)
            logger.warning(
                "Circuit breaker открыт для %s (%s): %d ошибок подряд, пауза",
                network, token_env, fails,
            )
        else:
            self._save(network, token_env, {"fails": fails, "open_until": None})

    def _rate_limit_cooldown(self) -> float:
        low, high = self._config.rate_limit_cooldown_minutes
        return self._rng(low, high)

    def _rate_limit_cooldown_note(self, now: datetime.datetime) -> str:
        return (now + datetime.timedelta(minutes=self._config.rate_limit_cooldown_minutes[1])).isoformat()

    def _open(
        self, network: str, token_env: str, now: datetime.datetime, minutes: float, *, fails: int
    ) -> None:
        open_until = (now + datetime.timedelta(minutes=minutes)).isoformat()
        self._save(network, token_env, {"fails": fails, "open_until": open_until})

    def _key(self, network: str, token_env: str) -> str:
        return f"breaker:{network}:{token_env}"

    def _load(self, network: str, token_env: str) -> dict:
        raw = self._repo.get_setting(self._key(network, token_env))
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _save(self, network: str, token_env: str, state: dict) -> None:
        self._repo.set_setting(self._key(network, token_env), json.dumps(state))
