"""Управление жизненным циклом headless-сервиса (старт/стоп/статус) из любого UI.

Общий примитив для веб-лаунчера и Telegram-бота: оборачивает
headless_service.run_forever в фоновый поток со своим asyncio-циклом. Остановка —
через stop_event, потокобезопасно (loop.call_soon_threadsafe), т.к. вызывающий
код (Flask/aiogram) живёт в другом потоке.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import threading

from dotenv import load_dotenv

from app.core.llm.client import LLMClient
from app.db.models import ProcessedPost
from app.db.repository import Repository, init_db, make_engine
from app.headless_service import run_forever
from app.logging_setup import setup_logging

logger = logging.getLogger("app")


class ServiceController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._started_at: datetime.datetime | None = None
        self._repo: Repository | None = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """True — запущено этим вызовом, False — уже было запущено."""
        with self._lock:
            if self.is_running():
                return False
            load_dotenv()
            config = self._load_config()
            setup_logging(config.logging)
            engine = make_engine()
            init_db(engine)
            self._repo = Repository(engine)
            # Loop и stop_event создаём здесь (синхронно), а не внутри потока — иначе
            # быстрый stop() сразу после start() мог не найти их (ещё None) и висел бы
            # весь join-timeout, не сумев послать сигнал остановки.
            self._loop = asyncio.new_event_loop()
            self._stop_event = asyncio.Event()
            self._thread = threading.Thread(
                target=self._run_service, args=(config,), daemon=True
            )
            self._thread.start()
            self._started_at = datetime.datetime.now(datetime.timezone.utc)
            return True

    def stop(self) -> bool:
        """True — остановлено этим вызовом, False — уже было остановлено."""
        with self._lock:
            if not self.is_running():
                return False
            if self._loop is not None and self._stop_event is not None:
                self._loop.call_soon_threadsafe(self._stop_event.set)
            self._thread.join(timeout=15)  # type: ignore[union-attr]
            self._thread = None
            return True

    @property
    def started_at(self) -> datetime.datetime | None:
        return self._started_at

    def recent_published(self, limit: int = 10) -> list[ProcessedPost]:
        return self._repo.list_recent_published(limit=limit) if self._repo is not None else []

    def _load_config(self):
        from app.config.loader import load_config

        return load_config()

    def _run_service(self, config) -> None:
        asyncio.set_event_loop(self._loop)
        # repo → правки шаблонов из бота подхватываются боевым пайплайном
        llm_client = LLMClient(config.llm, repo=self._repo)
        try:
            self._loop.run_until_complete(
                run_forever(self._repo, config, llm_client, stop_event=self._stop_event)
            )
        except Exception:
            logger.exception("Headless-сервис упал с ошибкой")
        finally:
            self._loop.close()
