"""Очередь публикации — ручная публикация по нажатию кнопки (критерий готовности MVP)."""
from __future__ import annotations

import asyncio

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.publishing.footer import FooterLinks
from app.core.publishing.queue_service import publish_queued_post
from app.core.publishing.telegram_publisher import TelegramPublisher
from app.db.repository import Repository

QUEUE_TABLE_HEADERS = ["Заголовок", "Score", "Статус"]
POST_ID_ROLE = Qt.ItemDataRole.UserRole


class PublishingPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._repo: Repository | None = None
        self._publisher: TelegramPublisher | None = None
        self._chat_id: str | None = None
        self._footer_links: FooterLinks | None = None

        self.table = QTableWidget(0, len(QUEUE_TABLE_HEADERS))
        self.table.setHorizontalHeaderLabels(QUEUE_TABLE_HEADERS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)

        self.refresh_button = QPushButton("Обновить")
        self.publish_button = QPushButton("Опубликовать выбранный")
        self.refresh_button.clicked.connect(self.refresh)
        self.publish_button.clicked.connect(self._on_publish)

        buttons_layout = QHBoxLayout()
        buttons_layout.addWidget(self.refresh_button)
        buttons_layout.addWidget(self.publish_button)

        layout = QVBoxLayout()
        layout.addWidget(self.table)
        layout.addLayout(buttons_layout)
        self.setLayout(layout)

    def bind(
        self,
        repo: Repository,
        publisher: TelegramPublisher,
        chat_id: str,
        footer_links: FooterLinks | None = None,
    ) -> None:
        self._repo = repo
        self._publisher = publisher
        self._chat_id = chat_id
        self._footer_links = footer_links
        self.refresh()

    def refresh(self) -> None:
        if self._repo is None:
            return
        posts = self._repo.list_processed_posts(status="queued")
        self.table.setRowCount(len(posts))
        for row, post in enumerate(posts):
            self.table.setItem(row, 0, QTableWidgetItem(post.headline or "(без заголовка)"))
            self.table.setItem(row, 1, QTableWidgetItem(str(post.score)))
            self.table.setItem(row, 2, QTableWidgetItem(post.status))
            self.table.item(row, 0).setData(POST_ID_ROLE, post.id)

    def _on_publish(self) -> None:
        if self._repo is None or self._publisher is None:
            return
        row = self.table.currentRow()
        if row < 0:
            return
        post_id = self.table.item(row, 0).data(POST_ID_ROLE)

        result = asyncio.run(
            publish_queued_post(
                self._repo,
                self._publisher,
                post_id=post_id,
                chat_id=self._chat_id,
                footer_links=self._footer_links,
            )
        )
        if result.success:
            QMessageBox.information(self, "Готово", "Пост опубликован")
        else:
            QMessageBox.warning(self, "Ошибка публикации", result.error or "неизвестная ошибка")
        self.refresh()
