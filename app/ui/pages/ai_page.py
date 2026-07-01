"""ИИ — редактор промптов и статус LLM (раздел 10.2, 9.2 SPEC.md)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.llm.client import LLMClient
from app.paths import PROMPTS_DIR

PROMPT_PATH_ROLE = Qt.ItemDataRole.UserRole


class AIPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._llm_client: LLMClient | None = None

        self.prompt_list = QListWidget()
        self.editor = QTextEdit()
        self.save_button = QPushButton("Сохранить промпт")
        self.llm_status_label = QLabel("Статус LLM: неизвестен")
        self.check_llm_button = QPushButton("Проверить LLM")

        self.prompt_list.currentItemChanged.connect(self._on_prompt_selected)
        self.save_button.clicked.connect(self._on_save)
        self.check_llm_button.clicked.connect(self._on_check_llm)

        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel("Промпты (/prompts/*.txt)"))
        left_layout.addWidget(self.prompt_list)

        right_layout = QVBoxLayout()
        right_layout.addWidget(self.editor)
        right_layout.addWidget(self.save_button)

        editor_row = QHBoxLayout()
        editor_row.addLayout(left_layout, stretch=1)
        editor_row.addLayout(right_layout, stretch=2)

        status_row = QHBoxLayout()
        status_row.addWidget(self.llm_status_label)
        status_row.addWidget(self.check_llm_button)

        layout = QVBoxLayout()
        layout.addLayout(status_row)
        layout.addLayout(editor_row)
        self.setLayout(layout)

        self._reload_prompt_list()

    def bind_llm_client(self, llm_client: LLMClient) -> None:
        self._llm_client = llm_client

    def _reload_prompt_list(self) -> None:
        self.prompt_list.clear()
        for path in sorted(PROMPTS_DIR.glob("*.txt")):
            item = QListWidgetItem(path.stem)
            item.setData(PROMPT_PATH_ROLE, str(path))
            self.prompt_list.addItem(item)

    def _on_prompt_selected(self, current: QListWidgetItem | None, _previous) -> None:
        if current is None:
            self.editor.clear()
            return
        path = current.data(PROMPT_PATH_ROLE)
        self.editor.setPlainText(open(path, encoding="utf-8").read())

    def _on_save(self) -> None:
        current = self.prompt_list.currentItem()
        if current is None:
            return
        path = current.data(PROMPT_PATH_ROLE)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.editor.toPlainText())

    def _on_check_llm(self) -> None:
        if self._llm_client is None:
            self.llm_status_label.setText("Статус LLM: клиент не настроен")
            return
        if not self._llm_client.is_running():
            self.llm_status_label.setText("⛔ LLM недоступна")
            return
        if not self._llm_client.is_model_downloaded():
            self.llm_status_label.setText("⛔ модель не скачана")
            return
        self.llm_status_label.setText("✅ LLM готова")
