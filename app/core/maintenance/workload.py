"""Тяжёлая работа с медиа — строго по одной за раз.

Замер на проде 2026-08-11 (консоль провайдера, SSH уже не пускал):

    Mem:  961 total, 614 used, 154 free
    Swap: 2047 total, 1254 used

То есть спрос на память под два гигабайта при 961 МБ физической. Диск при этом занят
на 63% — дело было не в нём. Машина колотилась в свопе, и всё, чему нужны новые
страницы, ждало диск минутами: сюда попал и форк sshd на аутентификации, из-за чего
на сервер стало не зайти.

Откуда спрос: у сервиса ЧЕТЫРЕ независимых расписания (цикл публикации, дневной фильм,
публикатор клипов, уборка), и пересекаться им никто не мешал. Фильм — это yt-dlp на
сотни мегабайт плюс ffmpeg; клип — ещё один ffmpeg; цикл — LLM, Pillow и загрузка медиа
в VK. Два таких процесса, наложившись, физическую память переполняют гарантированно.

Здесь не блокировка, а ПРОПУСК ТИКА: расписания периодические, и джоб, не пустой сейчас,
вернётся через 10–15 минут. Ждать в очереди было бы хуже — очередь из ждущих джобов и
есть тот самый одновременный расход памяти, от которого мы уходим.

Проверка и снятие флага происходят в потоке цикла событий (APScheduler здесь
asyncio-шный), а сама работа уезжает в `to_thread`, поэтому гонки на самом флаге нет и
мьютекс не нужен.
"""
from __future__ import annotations

import datetime
import logging
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger("app")

MAX_HOLD_MINUTES = 90
"""Сколько слот может быть занят, прежде чем считается зависшим.

Без этого предела слот сам становится источником простоя: джоб, повисший на сетевом
вызове без таймаута (yt-dlp на медленной закачке, заливка видео в VK), держал бы его
вечно, и публикация встала бы ЦЕЛИКОМ — ровно тот перебой, ради устранения которого
слот и вводился.

90 минут взяты с запасом к самой долгой честной работе: фильм на 650 МБ качается и
режется на клипы примерно час на этом VPS. Всё, что дольше, — уже не работа, а зависание."""


class MediaWorkGuard:
    """Один слот на всю тяжёлую работу с медиа."""

    def __init__(self) -> None:
        self._busy_with: str | None = None
        self._taken_at: datetime.datetime | None = None

    @property
    def busy_with(self) -> str | None:
        return self._busy_with

    def _is_stale(self, now: datetime.datetime) -> bool:
        if self._taken_at is None:
            return False
        return (now - self._taken_at) > datetime.timedelta(minutes=MAX_HOLD_MINUTES)

    @contextmanager
    def slot(self, name: str, now: datetime.datetime | None = None) -> Iterator[bool]:
        """`with guard.slot("фильм") as taken:` — taken=False, значит слот занят.

        Контекст-менеджер, а не пара acquire/release: слот обязан освободиться и при
        исключении внутри джоба, иначе одна ошибка в фильме навсегда останавливала бы
        клипы и публикацию.

        Зависший слот отбирается силой. Держать его вечно нельзя: смысл слота — не
        допустить одновременной тяжёлой работы, а не остановить публикации навсегда,
        если один джоб залип на вызове без таймаута."""
        now = now or datetime.datetime.utcnow()
        if self._busy_with is not None and not self._is_stale(now):
            logger.info("Джоб «%s» пропускает тик: занято — «%s»", name, self._busy_with)
            yield False
            return
        if self._busy_with is not None:
            logger.error(
                "Слот отобран у зависшего джоба «%s» (держит дольше %d мин) — забирает «%s»",
                self._busy_with, MAX_HOLD_MINUTES, name,
            )
        self._busy_with = name
        self._taken_at = now
        try:
            yield True
        finally:
            # Слот освобождает только тот, кто им сейчас владеет: у отобранного джоба
            # свой finally отработает позже, и без этой проверки он снял бы чужой слот.
            if self._busy_with == name:
                self._busy_with = None
                self._taken_at = None
