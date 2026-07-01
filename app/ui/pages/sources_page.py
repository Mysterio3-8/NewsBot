"""Источники — Telegram/VK (раздел 6.2 SPEC.md)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.db.models import Source
from app.db.repository import Repository

SOURCE_TABLE_HEADERS = ["Название", "Ссылка", "Приоритет", "Включён"]
SOURCE_ID_ROLE = Qt.ItemDataRole.UserRole


class SourceDialog(QDialog):
    """Форма добавления/редактирования источника."""

    def __init__(self, source: Source | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Источник")

        self.name_input = QLineEdit(source.name if source else "")
        self.url_input = QLineEdit(source.url if source else "")
        self.priority_input = QSpinBox()
        self.priority_input.setRange(0, 10)
        self.priority_input.setValue(source.priority if source else 5)
        self.enabled_input = QCheckBox()
        self.enabled_input.setChecked(source.enabled if source else True)

        form = QFormLayout()
        form.addRow("Название", self.name_input)
        form.addRow("Ссылка", self.url_input)
        form.addRow("Приоритет (0-10)", self.priority_input)
        form.addRow("Включён", self.enabled_input)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def values(self) -> dict:
        return {
            "name": self.name_input.text().strip(),
            "url": self.url_input.text().strip(),
            "priority": self.priority_input.value(),
            "enabled": self.enabled_input.isChecked(),
        }


class SourceListWidget(QWidget):
    """Таблица источников одного типа (Telegram или VK) с CRUD-кнопками."""

    def __init__(self, source_type: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source_type = source_type
        self._repo: Repository | None = None

        self.table = QTableWidget(0, len(SOURCE_TABLE_HEADERS))
        self.table.setHorizontalHeaderLabels(SOURCE_TABLE_HEADERS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)

        self.add_button = QPushButton("Добавить")
        self.edit_button = QPushButton("Редактировать")
        self.delete_button = QPushButton("Удалить")
        self.check_button = QPushButton("Проверить")

        self.add_button.clicked.connect(self._on_add)
        self.edit_button.clicked.connect(self._on_edit)
        self.delete_button.clicked.connect(self._on_delete)

        buttons_layout = QHBoxLayout()
        buttons_layout.addWidget(self.add_button)
        buttons_layout.addWidget(self.edit_button)
        buttons_layout.addWidget(self.delete_button)
        buttons_layout.addWidget(self.check_button)

        layout = QVBoxLayout()
        layout.addWidget(self.table)
        layout.addLayout(buttons_layout)
        self.setLayout(layout)

    def bind_repository(self, repo: Repository) -> None:
        self._repo = repo
        self.refresh()

    def refresh(self) -> None:
        if self._repo is None:
            return
        sources = self._repo.list_sources(source_type=self._source_type)
        self.table.setRowCount(len(sources))
        for row, source in enumerate(sources):
            self.table.setItem(row, 0, QTableWidgetItem(source.name))
            self.table.setItem(row, 1, QTableWidgetItem(source.url))
            self.table.setItem(row, 2, QTableWidgetItem(str(source.priority)))
            self.table.setItem(row, 3, QTableWidgetItem("да" if source.enabled else "нет"))
            self.table.item(row, 0).setData(SOURCE_ID_ROLE, source.id)

    def _selected_source_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        return self.table.item(row, 0).data(SOURCE_ID_ROLE)

    def _on_add(self) -> None:
        if self._repo is None:
            return
        dialog = SourceDialog(parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["name"] or not values["url"]:
            QMessageBox.warning(self, "Ошибка", "Название и ссылка обязательны")
            return
        self._repo.create_source(
            type=self._source_type,
            name=values["name"],
            url=values["url"],
            priority=values["priority"],
        )
        self.refresh()

    def _on_edit(self) -> None:
        source_id = self._selected_source_id()
        if source_id is None or self._repo is None:
            return
        current_source = self._repo.get_source(source_id)
        dialog = SourceDialog(source=current_source, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        self._repo.update_source(source_id, **dialog.values())
        self.refresh()

    def _on_delete(self) -> None:
        source_id = self._selected_source_id()
        if source_id is None or self._repo is None:
            return
        self._repo.delete_source(source_id)
        self.refresh()


class SourcesPage(QTabWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.telegram_tab = SourceListWidget(source_type="tg")
        self.vk_tab = SourceListWidget(source_type="vk")
        self.addTab(self.telegram_tab, "Telegram")
        self.addTab(self.vk_tab, "VK")

    def bind_repository(self, repo: Repository) -> None:
        self.telegram_tab.bind_repository(repo)
        self.vk_tab.bind_repository(repo)
