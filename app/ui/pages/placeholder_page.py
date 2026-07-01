"""Общая заглушка для страниц, чья логика приходит на следующих этапах."""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class PlaceholderPage(QWidget):
    def __init__(self, title: str, stage_note: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        label = QLabel(f"{title}\n\n{stage_note}")
        layout = QVBoxLayout()
        layout.addWidget(label)
        layout.addStretch()
        self.setLayout(layout)
