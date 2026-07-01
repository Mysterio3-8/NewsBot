"""Главное окно — боковое меню + стек страниц (раздел 6 SPEC.md)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QHBoxLayout, QListWidget, QMainWindow, QStackedWidget, QWidget

from app.config.loader import AppConfig
from app.core.llm.client import LLMClient
from app.core.publishing.footer import build_footer_links_from_config
from app.db.repository import Repository
from app.factories import build_telegram_publisher
from app.ui.pages.ai_page import AIPage
from app.ui.pages.home_page import HomePage
from app.ui.pages.logs_page import LogsPage
from app.ui.pages.placeholder_page import PlaceholderPage
from app.ui.pages.publishing_page import PublishingPage
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

        self.home_page = HomePage()
        self.sources_page = SourcesPage()
        self.ai_page = AIPage()
        self.images_page = PlaceholderPage(
            "🖼 Изображения", "Приоритет провайдеров и watermark — см. Настройки → Изображения"
        )
        self.publishing_page = PublishingPage()
        self.scheduler_page = PlaceholderPage(
            "📅 Планировщик", "Автозапуск по расписанию — доступен в headless-режиме (Этап 5)"
        )
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
        llm_client = LLMClient(config.llm)
        publisher = build_telegram_publisher(config)
        chat_id = config.publishing.telegram.destination
        footer_links = build_footer_links_from_config(config.footer)

        self.home_page.bind_repository(repo)
        self.sources_page.bind_repository(repo)
        self.ai_page.bind_llm_client(llm_client)
        self.settings_page.bind_config(config, config_path)
        if publisher is not None:
            self.publishing_page.bind(repo, publisher, chat_id, footer_links)
