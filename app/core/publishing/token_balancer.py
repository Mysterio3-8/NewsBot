"""Балансер личных (user) VK-токенов — размазывает загрузки медиа по пулу токенов.

Зачем: личный токен банится за объём (прецедент 2026-07-02 — бан после 12 публикаций
подряд). При 24 фильмах/сутки у Кино один токен гарантированно словит бан, поэтому
загрузки распределяются между несколькими токенами: берётся наименее нагруженный
сегодня, токен с исчерпанным суточным капом или в кулдауне (после ошибки VK)
пропускается.

Чистая логика без БД — состояние приходит снаружи (dict), это делает её тестируемой
и позволяет хранить счётчики где угодно (сейчас — settings-таблица, JSON).
"""
from __future__ import annotations

import dataclasses
import datetime
import json

DEFAULT_PER_TOKEN_DAILY_CAP = 20
"""Сколько загрузок в сутки допускаем на ОДИН личный токен. Консервативно: VK банит
за объём, а не за сам факт. 24 фильма на 2 токенах = по 12 на каждый."""

COOLDOWN_MINUTES_AFTER_ERROR = 90
"""На сколько выводим токен из ротации после ошибки VK (подозрение на лимит/бан)."""


@dataclasses.dataclass(frozen=True)
class TokenState:
    """Состояние одного токена на текущие сутки."""

    env_name: str
    used_today: int = 0
    day: str = ""
    cooling_until: str | None = None  # ISO-строка UTC

    def is_cooling(self, now: datetime.datetime) -> bool:
        if not self.cooling_until:
            return False
        return now < datetime.datetime.fromisoformat(self.cooling_until)


def _today(now: datetime.datetime) -> str:
    return now.date().isoformat()


def load_states(raw: str | None) -> dict[str, TokenState]:
    """Разбор JSON из БД. Битые данные не роняют публикацию — считаем, что счётчиков нет."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    states: dict[str, TokenState] = {}
    for env_name, item in data.items():
        if not isinstance(item, dict):
            continue
        states[env_name] = TokenState(
            env_name=env_name,
            used_today=item.get("used_today", 0),
            day=item.get("day", ""),
            cooling_until=item.get("cooling_until"),
        )
    return states


def dump_states(states: dict[str, TokenState]) -> str:
    return json.dumps(
        {
            s.env_name: {
                "used_today": s.used_today,
                "day": s.day,
                "cooling_until": s.cooling_until,
            }
            for s in states.values()
        },
        ensure_ascii=False,
    )


def _current(states: dict[str, TokenState], env_name: str, now: datetime.datetime) -> TokenState:
    """Состояние токена, сброшенное на ноль, если счётчик от прошлых суток."""
    state = states.get(env_name, TokenState(env_name=env_name))
    if state.day != _today(now):
        return TokenState(env_name=env_name, used_today=0, day=_today(now),
                          cooling_until=state.cooling_until)
    return state


def pick_token(
    pool: list[str],
    states: dict[str, TokenState],
    *,
    now: datetime.datetime,
    per_token_daily_cap: int = DEFAULT_PER_TOKEN_DAILY_CAP,
) -> str | None:
    """Наименее нагруженный сегодня токен из пула. None — все исчерпаны или в кулдауне
    (вызывающий код тогда пропускает загрузку медиа, а не публикует чем попало)."""
    available = []
    for env_name in pool:
        state = _current(states, env_name, now)
        if state.is_cooling(now):
            continue
        if state.used_today >= per_token_daily_cap:
            continue
        available.append((state.used_today, pool.index(env_name), env_name))
    if not available:
        return None
    available.sort()  # меньше использован → раньше в пуле (стабильный порядок)
    return available[0][2]


def record_use(
    states: dict[str, TokenState], env_name: str, now: datetime.datetime
) -> dict[str, TokenState]:
    """Новый словарь состояний с +1 использованием (иммутабельно)."""
    state = _current(states, env_name, now)
    updated = dict(states)
    updated[env_name] = dataclasses.replace(
        state, used_today=state.used_today + 1, day=_today(now)
    )
    return updated


def record_error(
    states: dict[str, TokenState],
    env_name: str,
    now: datetime.datetime,
    *,
    cooldown_minutes: int = COOLDOWN_MINUTES_AFTER_ERROR,
) -> dict[str, TokenState]:
    """Вывести токен из ротации на cooldown_minutes — после ошибки VK (лимит/бан)."""
    state = _current(states, env_name, now)
    until = (now + datetime.timedelta(minutes=cooldown_minutes)).isoformat()
    updated = dict(states)
    updated[env_name] = dataclasses.replace(state, day=_today(now), cooling_until=until)
    return updated


def render_report(
    pool: list[str], states: dict[str, TokenState], now: datetime.datetime,
    *, per_token_daily_cap: int = DEFAULT_PER_TOKEN_DAILY_CAP,
) -> str:
    """Сводка для бота: сколько загрузок на каждом токене сегодня."""
    if not pool:
        return "Пул токенов пуст."
    lines = []
    for env_name in pool:
        state = _current(states, env_name, now)
        mark = "❄️" if state.is_cooling(now) else ("🔴" if state.used_today >= per_token_daily_cap else "🟢")
        lines.append(f"{mark} {env_name}: {state.used_today}/{per_token_daily_cap}")
    return "\n".join(lines)
