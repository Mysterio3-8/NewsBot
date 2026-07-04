"""Главная страница — статус приложения (раздел 6.1 SPEC.md)."""
from __future__ import annotations

import asyncio
import datetime
from collections.abc import Awaitable, Callable

from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from app.core.scheduler import start_of_today_utc
from app.db.repository import Repository

CheckNowCallback = Callable[[], Awaitable[None]]


class HomePage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._repo: Repository | None = None
        self._on_check_now: CheckNowCallback | None = None
        self._on_start_scheduler: Callable[[], None] | None = None
        self._on_stop_scheduler: Callable[[], None] | None = None

        self.status_label = QLabel("Статус: остановлено")
        self.last_check_label = QLabel("Последняя проверка: —")
        self.next_check_label = QLabel("Следующая проверка: —")
        self.new_posts_label = QLabel("Новых постов: 0")
        self.published_today_label = QLabel("Опубликовано сегодня: 0")
        self.errors_label = QLabel("Ошибок: 0")

        self.start_button = QPushButton("Запустить")
        self.stop_button = QPushButton("Остановить")
        self.check_now_button = QPushButton("Проверить сейчас")
        self.stop_button.setEnabled(False)

        self.start_button.clicked.connect(self._on_start)
        self.stop_button.clicked.connect(self._on_stop)
        self.check_now_button.clicked.connect(self.run_check_now)

        buttons_layout = QHBoxLayout()
        buttons_layout.addWidget(self.start_button)
        buttons_layout.addWidget(self.stop_button)
        buttons_layout.addWidget(self.check_now_button)

        layout = QVBoxLayout()
        layout.addWidget(self.status_label)
        layout.addWidget(self.last_check_label)
        layout.addWidget(self.next_check_label)
        layout.addWidget(self.new_posts_label)
        layout.addWidget(self.published_today_label)
        layout.addWidget(self.errors_label)
        layout.addLayout(buttons_layout)
        layout.addStretch()
        self.setLayout(layout)

    def bind_repository(self, repo: Repository) -> None:
        self._repo = repo
        self.refresh_counts()

    def bind_check_now(self, callback: CheckNowCallback) -> None:
        self._on_check_now = callback

    def bind_scheduler_controls(
        self, *, on_start: Callable[[], None], on_stop: Callable[[], None]
    ) -> None:
        self._on_start_scheduler = on_start
        self._on_stop_scheduler = on_stop

    def refresh_counts(self) -> None:
        if self._repo is None:
            return
        queued_count = len(self._repo.list_processed_posts(status="queued"))
        published_today = self._repo.count_published_since(start_of_today_utc())
        rejected_count = len(self._repo.list_processed_posts(status="failed"))

        self.new_posts_label.setText(f"Новых постов: {queued_count}")
        self.published_today_label.setText(f"Опубликовано сегодня: {published_today}")
        self.errors_label.setText(f"Ошибок: {rejected_count}")

    def run_check_now(self) -> None:
        if self._on_check_now is None:
            QMessageBox.warning(self, "Не настроено", "Мониторинг источников не сконфигурирован")
            return

        self.check_now_button.setEnabled(False)
        self.check_now_button.setText("Проверяю...")
        try:
            asyncio.run(self._on_check_now())
        except Exception as error:
            QMessageBox.warning(self, "Ошибка проверки", str(error))
        finally:
            self.check_now_button.setEnabled(True)
            self.check_now_button.setText("Проверить сейчас")

        now = datetime.datetime.now().strftime("%H:%M:%S")
        self.last_check_label.setText(f"Последняя проверка: {now}")
        self.refresh_counts()

    def _on_start(self) -> None:
        self.status_label.setText("Статус: работает")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        if self._on_start_scheduler is not None:
            self._on_start_scheduler()

    def _on_stop(self) -> None:
        self.status_label.setText("Статус: остановлено")
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.next_check_label.setText("Следующая проверка: —")
        if self._on_stop_scheduler is not None:
            self._on_stop_scheduler()
