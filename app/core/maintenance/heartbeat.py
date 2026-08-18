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
    """Сколько РАБОЧИХ часов без публикаций считается нормой.

    Считается от РЕАЛЬНОГО темпа софта с запасом примерно вдвое: тревога должна
    сработать на поломке, а не на случайно растянувшемся интервале."""
    work_start_hour: int = 0
    work_end_hour: int = 24
    """Окно работы софта в МСК (ТЗ владельца 2026-08-14: софты разведены по времени суток,
    чтобы не драться за единственный личный VK-аккаунт).

    🔴 Без него сторож считал тишину по КАЛЕНДАРНЫМ часам и врал каждый день. Новости
    работают 9 часов из 24 — значит 15 часов они молчат ШТАТНО, а порог стоял 6, и
    тревога уходила владельцу ежедневно. Ровно поэтому он и сказал «меня бесят эти
    алерты»: сторож, который кричит всегда, не замечают, когда он кричит по делу."""


# Пороги посчитаны от суточных объёмов (см. all_auto/CLAUDE.md):
# Новости 10/сутки (~2.4 ч между постами), Кино 7 (~3.5 ч), Музыка 4 (~6 ч),
# Минусы 1 (24 ч). Берём примерно двойной запас.
# Пороги считаются в РАБОЧИХ часах окна (ТЗ 2026-08-16 по объёмам):
# Новости 3 поста за 9 ч окна (~3 ч между постами) → 6; Кино фильм + 3 поста + 2 клипа
# за 15 ч (~2.5 ч) → 6; Музыка 2 трека + 2 сборника за 9 ч (~2.2 ч) → 7 (сборник
# собирается полчаса, запас нужен); Минусы 1 в сутки → 20 рабочих часов, это больше двух
# его окон подряд. Везде примерно двойной запас к реальному темпу.
DEFAULT_WATCHLIST = (
    WatchedCommunity("Новости", 233689032, max_silence_hours=6, work_start_hour=0, work_end_hour=9),
    WatchedCommunity("Кино", 240120678, max_silence_hours=6, work_start_hour=9, work_end_hour=24),
    # Музыка с 2026-08-18 работает 08:00–01:00 МСК (ТЗ «растянуть на весь день»):
    # окно 17 часов, интервал каждого потока 420–560 мин, то есть штатный разрыв между
    # записями доходит до девяти рабочих часов. Порог 11 — двойной запас к обычному
    # темпу и всё ещё меньше одного окна, поэтому вставший софт заметен в тот же день.
    WatchedCommunity("Infinity Music", 240295467, max_silence_hours=11, work_start_hour=8, work_end_hour=1),
    WatchedCommunity("Минусы", 234048994, max_silence_hours=20, work_start_hour=0, work_end_hour=9),
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


MOSCOW_OFFSET = datetime.timedelta(hours=3)


def working_hours_between(
    start: datetime.datetime, end: datetime.datetime, work_start: int, work_end: int
) -> float:
    """Сколько РАБОЧИХ часов прошло между двумя моментами (оба в UTC).

    Часы вне окна софта не считаются: он в это время молчит по расписанию, а не потому
    что сломался. Идём по получасам — точность выше любой разумной, а окно может
    пересекать полночь, и аналитическая формула для такого случая читается хуже, чем
    цикл, который просто спрашивает «этот час рабочий?».

    Окно на все сутки (0..24) даёт ровно календарную разницу — прежнее поведение."""
    if work_start == 0 and work_end >= 24:
        return (end - start).total_seconds() / 3600
    step = datetime.timedelta(minutes=30)
    total = 0.0
    moment = start
    while moment < end:
        hour = (moment + MOSCOW_OFFSET).hour
        inside = (
            work_start <= hour < work_end
            if work_start < work_end
            else hour >= work_start or hour < work_end
        )
        if inside:
            total += step.total_seconds() / 3600
        moment += step
    return total


def silence_hours(
    moment: datetime.datetime | None,
    now: datetime.datetime,
    work_start: int = 0,
    work_end: int = 24,
) -> float:
    """Сколько РАБОЧИХ часов сообщество молчит. Записей нет вовсе → бесконечность."""
    if moment is None:
        return float("inf")
    return working_hours_between(moment, now, work_start, work_end)


def build_silence_alert(stale: list[tuple[WatchedCommunity, float]]) -> str:
    """Текст тревоги. Пишем и порог тоже — иначе непонятно, много это или норма."""
    lines = ["🔇 Софт молчит дольше обычного:"]
    for community, hours in stale:
        measured = "записей нет вовсе" if hours == float("inf") else f"{hours:.0f} ч"
        lines.append(
            f"• {community.name}: {measured} рабочего времени "
            f"(норма до {community.max_silence_hours} ч, окно "
            f"{community.work_start_hour}:00–{community.work_end_hour}:00 МСК)"
        )
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
        hours = silence_hours(
            last_post_moment(items), now, community.work_start_hour, community.work_end_hour
        )
        if hours > community.max_silence_hours:
            stale.append((community, hours))
            logger.warning(
                "Сторож: %s молчит %.0f ч (порог %d)",
                community.name, hours, community.max_silence_hours,
            )
    return stale
