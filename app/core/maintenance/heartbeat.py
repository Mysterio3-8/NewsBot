"""Сторож тишины: софт перестал публиковать — владелец узнаёт в тот же час.

ТЗ владельца 2026-08-12: «чтобы софты без перебоя работали всегда».

Почему именно так. Ни один простой этой недели не был замечен софтом самим:

* треки Infinity Music встали 09.08 — очередь опустела, и никто не заметил двое суток;
* сборники встали 11.08 — упавшие плейлисты не возвращались в очередь, тишина полтора
  суток;
* Кино и Новости стояли 04.08, пока голодал пул токенов.

Каждый раз поломку находил владелец глазами по стене сообщества, через сутки и позже.
Юнит-тесты при этом были зелёными: все три случая — это пустая очередь или занятый
внешний ресурс, а не ошибка в коде.

**Проверяем РЕЗУЛЬТАТ, а не намерение.** Смотрим стену сообщества через VK API, а не
свои журналы и не свои БД. Софт может считать, что опубликовал, — а записи в сообществе
не быть (ровно так выглядел провал загрузки медиа 04.08). И это единственный способ,
одинаково работающий для всех четырёх софтов: у них разные БД, разные схемы и разные
репозитории, а стена у всех одна и та же сущность.

⚠️ `wall.get` требует ЛИЧНЫЙ токен: групповой отдаёт `[27] Group authorization failed`.
Это чтение, один запрос на сообщество в час — на фоне суточных лимитов публикации
пренебрежимо, но дёргать чаще незачем.
"""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass

logger = logging.getLogger("app")

VK_API_VERSION = "5.199"


@dataclass(frozen=True)
class WatchedCommunity:
    """Сообщество под присмотром и допустимая для него тишина."""

    name: str
    group_id: int
    max_silence_hours: int
    """Сколько часов без публикаций считается нормой.

    Считается от РЕАЛЬНОГО темпа софта с запасом примерно вдвое: тревога должна
    сработать на поломке, а не на случайно растянувшемся интервале."""


# Пороги посчитаны от суточных объёмов (см. all_auto/CLAUDE.md):
# Новости 10/сутки (~2.4 ч между постами), Кино 7 (~3.5 ч), Музыка 4 (~6 ч),
# Минусы 1 (24 ч). Берём примерно двойной запас.
DEFAULT_WATCHLIST = (
    WatchedCommunity("Новости", 233689032, max_silence_hours=6),
    WatchedCommunity("Кино", 240120678, max_silence_hours=8),
    WatchedCommunity("Infinity Music", 240295467, max_silence_hours=14),
    WatchedCommunity("Минусы", 234048994, max_silence_hours=30),
)


def last_post_moment(items: list[dict]) -> datetime.datetime | None:
    """Момент последней СВОЕЙ записи сообщества.

    Репосты (`copy_history`) пропускаем: владелец руками репостит анонсы розыгрышей в
    несколько сообществ сразу, и такой репост маскировал бы мёртвый софт — стена
    выглядела бы живой, хотя автопостинг стоит.

    Закреплённая запись `is_pinned` в выдаче идёт ПЕРВОЙ независимо от даты, поэтому
    берём максимум по дате, а не первый элемент."""
    dates = [
        int(item["date"])
        for item in items
        if item.get("date") and not item.get("copy_history")
    ]
    if not dates:
        return None
    return datetime.datetime.utcfromtimestamp(max(dates))


def silence_hours(moment: datetime.datetime | None, now: datetime.datetime) -> float:
    """Сколько часов сообщество молчит. Записей нет вовсе → бесконечность."""
    if moment is None:
        return float("inf")
    return (now - moment).total_seconds() / 3600


def build_silence_alert(stale: list[tuple[WatchedCommunity, float]]) -> str:
    """Текст тревоги. Пишем и порог тоже — иначе непонятно, много это или норма."""
    lines = ["🔇 Софт молчит дольше обычного:"]
    for community, hours in stale:
        measured = "записей нет вовсе" if hours == float("inf") else f"{hours:.0f} ч"
        lines.append(f"• {community.name}: {measured} (норма до {community.max_silence_hours} ч)")
    lines.append(
        "\nЧастые причины: пустая очередь, занят личный токен VK, упал внешний источник. "
        "Проверить: /status и /disk в этом боте."
    )
    return "\n".join(lines)


def fetch_wall_items(token: str, group_id: int, count: int = 10) -> list[dict]:
    """Последние записи стены. Ошибка сети или VK → пустой список.

    Fail-quiet осознанно: сторож не должен превращаться в источник собственных тревог.
    Недоступный VK — это уже видно по самим публикациям."""
    import requests

    try:
        response = requests.get(
            "https://api.vk.com/method/wall.get",
            params={
                "owner_id": -abs(group_id),
                "count": count,
                "access_token": token,
                "v": VK_API_VERSION,
            },
            timeout=30,
        ).json()
    except Exception as error:  # noqa: BLE001 — граница сети
        logger.warning("Сторож: стена %s не прочиталась: %s", group_id, error)
        return []
    if "error" in response:
        logger.warning(
            "Сторож: VK отказал по сообществу %s: [%s] %s",
            group_id,
            response["error"].get("error_code"),
            response["error"].get("error_msg"),
        )
        return []
    return response.get("response", {}).get("items", [])


def find_silent_communities(
    token: str,
    watchlist: tuple[WatchedCommunity, ...] = DEFAULT_WATCHLIST,
    *,
    now: datetime.datetime | None = None,
) -> list[tuple[WatchedCommunity, float]]:
    """Сообщества, молчащие дольше своего порога.

    Сообщество, стену которого не удалось прочитать, в список НЕ попадает: молчание VK
    про наши записи и молчание софта — разные вещи, и путать их значит слать ложные
    тревоги при каждом сбое сети."""
    now = now or datetime.datetime.utcnow()
    stale: list[tuple[WatchedCommunity, float]] = []
    for community in watchlist:
        items = fetch_wall_items(token, community.group_id)
        if not items:
            continue
        hours = silence_hours(last_post_moment(items), now)
        if hours > community.max_silence_hours:
            stale.append((community, hours))
            logger.warning(
                "Сторож: %s молчит %.0f ч (порог %d)",
                community.name, hours, community.max_silence_hours,
            )
    return stale
