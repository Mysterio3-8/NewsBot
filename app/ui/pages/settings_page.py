"""Настройки (раздел 16 SPEC.md). Вкладка «Фильтры» — редактируемая и сохраняемая
в config.yaml; остальные вкладки показывают текущие значения (только чтение).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.config.loader import AppConfig, ConfigValidationError, update_config_section


def parse_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


class FiltersSettingsWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config_path: Path | None = None

        self.min_score_input = QSpinBox()
        self.min_score_input.setRange(0, 100)
        self.min_views_input = QSpinBox()
        self.min_views_input.setRange(0, 1_000_000)
        self.similarity_input = QDoubleSpinBox()
        self.similarity_input.setRange(0.0, 1.0)
        self.similarity_input.setSingleStep(0.01)
        self.stop_words_input = QTextEdit()
        self.whitelist_input = QTextEdit()
        self.blacklist_input = QTextEdit()
        self.save_button = QPushButton("Сохранить фильтры")
        self.save_button.clicked.connect(self._on_save)

        form = QFormLayout()
        form.addRow("Порог публикации (min_score)", self.min_score_input)
        form.addRow("Мин. просмотров источника", self.min_views_input)
        form.addRow("Порог схожести дублей", self.similarity_input)
        form.addRow("Стоп-слова (по строке)", self.stop_words_input)
        form.addRow("Белый список (по строке)", self.whitelist_input)
        form.addRow("Чёрный список (по строке)", self.blacklist_input)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.save_button)
        self.setLayout(layout)

    def bind_config(self, config: AppConfig, config_path: Path) -> None:
        self._config_path = config_path
        filters = config.filters
        self.min_score_input.setValue(filters.min_score)
        self.min_views_input.setValue(filters.min_views)
        self.similarity_input.setValue(filters.duplicate_similarity_threshold)
        self.stop_words_input.setPlainText("\n".join(filters.stop_words))
        self.whitelist_input.setPlainText("\n".join(filters.whitelist_keywords))
        self.blacklist_input.setPlainText("\n".join(filters.blacklist_keywords))

    def _on_save(self) -> None:
        if self._config_path is None:
            return
        try:
            updated = update_config_section(
                self._config_path,
                "filters",
                min_score=self.min_score_input.value(),
                min_views=self.min_views_input.value(),
                duplicate_similarity_threshold=self.similarity_input.value(),
                stop_words=parse_lines(self.stop_words_input.toPlainText()),
                whitelist_keywords=parse_lines(self.whitelist_input.toPlainText()),
                blacklist_keywords=parse_lines(self.blacklist_input.toPlainText()),
            )
        except ConfigValidationError as error:
            QMessageBox.warning(self, "Некорректные настройки", str(error))
            return
        self.bind_config(updated, self._config_path)
        QMessageBox.information(self, "Готово", "Настройки фильтров сохранены")


class ReadOnlySectionWidget(QWidget):
    """Показывает секцию конфига как список пар ключ-значение, без редактирования."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QFormLayout()
        self.setLayout(self._layout)

    def show_fields(self, fields: dict[str, object]) -> None:
        while self._layout.rowCount():
            self._layout.removeRow(0)
        for key, value in fields.items():
            self._layout.addRow(key, QLabel(str(value)))


class SettingsPage(QTabWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.general_tab = ReadOnlySectionWidget()
        self.filters_tab = FiltersSettingsWidget()
        self.ai_tab = ReadOnlySectionWidget()
        self.images_tab = ReadOnlySectionWidget()
        self.publishing_tab = ReadOnlySectionWidget()
        self.logs_tab = ReadOnlySectionWidget()

        self.addTab(self.general_tab, "Общие")
        self.addTab(self.filters_tab, "Фильтры")
        self.addTab(self.ai_tab, "ИИ")
        self.addTab(self.images_tab, "Изображения")
        self.addTab(self.publishing_tab, "Публикация")
        self.addTab(self.logs_tab, "Логи")

    def bind_config(self, config: AppConfig, config_path: Path) -> None:
        self.general_tab.show_fields(
            {"Название": config.app.name, "Язык": config.app.language, "Часовой пояс": config.app.timezone}
        )
        self.filters_tab.bind_config(config, config_path)
        self.ai_tab.show_fields(
            {
                "Модель": config.llm.model,
                "Хост Ollama": config.llm.host,
                "Temperature": config.llm.temperature,
                "Top-p": config.llm.top_p,
            }
        )
        self.images_tab.show_fields(
            {
                "Порядок провайдеров": ", ".join(config.images.providers_order),
                "Соотношение сторон": config.images.target_aspect_ratio,
            }
        )
        self.publishing_tab.show_fields(
            {
                "TG чат": config.publishing.telegram.destination,
                "VK группа": config.publishing.vk.destination,
                "Режим расписания": config.publishing.schedule.mode,
                "Лимит постов в сутки": config.publishing.schedule.max_posts_per_day,
            }
        )
        self.logs_tab.show_fields(
            {"Уровень": config.logging.level, "Размер файла (МБ)": config.logging.max_file_size_mb}
        )
