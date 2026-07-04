"""Главное окно — боковое меню + стек страниц (раздел 6 SPEC.md)."""
from __future__ import annotations

import asyncio
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QMainWindow, QStackedWidget, QWidget

from app.config.loader import AppConfig
from app.core.check_cycle import run_check_cycle
from app.core.llm.client import LLMClient
from app.core.publishing.footer import build_footer_links_from_config
from app.db.repository import Repository
from app.factories import (
    build_image_providers,
    build_telegram_fetcher,
    build_telegram_publisher,
    build_vk_fetcher,
)
from app.ui.pages.ai_page import AIPage
from app.ui.pages.home_page import HomePage
from app.ui.pages.images_page import ImagesPage
from app.ui.pages.logs_page import LogsPage
from app.ui.pages.publishing_page import PublishingPage
from app.ui.pages.scheduler_page import SchedulerPage
from app.ui.pages.settings_page import SettingsPage
from app.ui.pages.sources_page import SourcesPage

MENU_ITEMS = [
    "🏠 Главная",
    "📡 Источники",
    "🤖 ИИ",
    "🖼 Изображения",
    "📤 Публикация",
    "📅 Планировщик",
    "⚙ Настройки",
    "📄 Логи",
]


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        config: AppConfig,
        config_path: Path,
        repo: Repository,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI News Rewriter")
        self.resize(1100, 700)

        self._check_timer = QTimer(self)
        self._check_timer.timeout.connect(self._run_scheduled_check)

        self.home_page = HomePage()
        self.sources_page = SourcesPage()
        self.ai_page = AIPage()
        self.images_page = ImagesPage()
        self.publishing_page = PublishingPage()
        self.scheduler_page = SchedulerPage()
        self.settings_page = SettingsPage()
        self.logs_page = LogsPage()

        self._bind_pages(config=config, config_path=config_path, repo=repo)

        self.menu_list = QListWidget()
        self.menu_list.addItems(MENU_ITEMS)
        self.menu_list.setFixedWidth(220)

        self.pages_stack = QStackedWidget()
        for page in [
            self.home_page,
            self.sources_page,
            self.ai_page,
            self.images_page,
            self.publishing_page,
            self.scheduler_page,
            self.settings_page,
            self.logs_page,
        ]:
            self.pages_stack.addWidget(page)

        self.menu_list.currentRowChanged.connect(self.pages_stack.setCurrentIndex)
        self.menu_list.setCurrentRow(0)

        central = QWidget()
        layout = QHBoxLayout()
        layout.addWidget(self.menu_list)
        layout.addWidget(self.pages_stack)
        central.setLayout(layout)
        self.setCentralWidget(central)

    def _bind_pages(self, *, config: AppConfig, config_path: Path, repo: Repository) -> None:
        self._config = config
        self._config_path = config_path
        self._repo = repo
        self._llm_client = LLMClient(config.llm)
        self._tg_fetcher = build_telegram_fetcher()
        self._vk_fetcher = build_vk_fetcher()
        self._image_providers = build_image_providers()

        publisher = build_telegram_publisher(config)
        chat_id = config.publishing.telegram.destination
        footer_links = build_footer_links_from_config(config.footer)

        self.home_page.bind_repository(repo)
        self.home_page.bind_check_now(self._run_check_now)
        self.home_page.bind_scheduler_controls(
            on_start=self._start_periodic_checks, on_stop=self._stop_periodic_checks
        )
        self.sources_page.bind_repository(repo)
        self.ai_page.bind_llm_client(self._llm_client)
        self.settings_page.bind_config(config, config_path)
        self.images_page.bind_config(config, config_path)
        self.scheduler_page.bind_config(config, config_path)
        if publisher is not None:
            self.publishing_page.bind(repo, publisher, chat_id, footer_links)

    async def _run_check_now(self) -> None:
        await run_check_cycle(
            self._repo,
            self._config,
            self._llm_client,
            tg_fetcher=self._tg_fetcher,
            vk_fetcher=self._vk_fetcher,
            image_providers=self._image_providers,
        )

    def _run_scheduled_check(self) -> None:
        asyncio.run(self._run_check_now())
        self.home_page.refresh_counts()

    def _start_periodic_checks(self) -> None:
        interval_ms = self._config.monitoring.check_interval_minutes * 60_000
        self._check_timer.start(interval_ms)

    def _stop_periodic_checks(self) -> None:
        self._check_timer.stop()
