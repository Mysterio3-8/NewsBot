"""Логи — просмотр последних строк с фильтром по файлу (раздел 17 SPEC.md)."""
from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.paths import LOGS_DIR

DEFAULT_TAIL_LINES = 200


class LogsPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.file_selector = QComboBox()
        self.file_selector.addItems(
            ["app.log", "llm.log", "monitoring.log", "publishing.log", "errors.log"]
        )
        self.refresh_button = QPushButton("Обновить")
        self.open_folder_button = QPushButton("Открыть папку логов")
        self.text_view = QTextEdit()
        self.text_view.setReadOnly(True)

        self.file_selector.currentTextChanged.connect(lambda _: self.refresh())
        self.refresh_button.clicked.connect(self.refresh)
        self.open_folder_button.clicked.connect(self._open_logs_folder)

        controls_layout = QHBoxLayout()
        controls_layout.addWidget(self.file_selector)
        controls_layout.addWidget(self.refresh_button)
        controls_layout.addWidget(self.open_folder_button)

        layout = QVBoxLayout()
        layout.addLayout(controls_layout)
        layout.addWidget(self.text_view)
        self.setLayout(layout)

    def refresh(self) -> None:
        log_path = LOGS_DIR / self.file_selector.currentText()
        if not log_path.exists():
            self.text_view.setPlainText("(файл лога ещё не создан)")
            return

        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        self.text_view.setPlainText("\n".join(lines[-DEFAULT_TAIL_LINES:]))

    def _open_logs_folder(self) -> None:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(LOGS_DIR)  # noqa: S606 (открытие локальной папки логов, не пользовательский ввод)
        else:
            subprocess.run(["xdg-open", str(LOGS_DIR)], check=False)
