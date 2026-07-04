"""Настройки (раздел 16 SPEC.md). Все вкладки редактируемые и сохраняются в config.yaml.
Изображения/watermark и расписание публикации вынесены в отдельные страницы навигации.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.config.loader import AppConfig, ConfigValidationError, update_config_section

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]


def parse_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


class GeneralSettingsWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config_path: Path | None = None

        self.name_input = QLineEdit()
        self.language_input = QLineEdit()
        self.timezone_input = QLineEdit()
        self.save_button = QPushButton("Сохранить")
        self.save_button.clicked.connect(self._on_save)

        form = QFormLayout()
        form.addRow("Название приложения", self.name_input)
        form.addRow("Язык", self.language_input)
        form.addRow("Часовой пояс", self.timezone_input)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.save_button)
        self.setLayout(layout)

    def bind_config(self, config: AppConfig, config_path: Path) -> None:
        self._config_path = config_path
        self.name_input.setText(config.app.name)
        self.language_input.setText(config.app.language)
        self.timezone_input.setText(config.app.timezone)

    def _on_save(self) -> None:
        if self._config_path is None:
            return
        try:
            update_config_section(
                self._config_path,
                "app",
                name=self.name_input.text(),
                language=self.language_input.text(),
                timezone=self.timezone_input.text(),
            )
        except ConfigValidationError as error:
            QMessageBox.warning(self, "Некорректные настройки", str(error))
            return
        QMessageBox.information(self, "Готово", "Общие настройки сохранены")


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


LLM_PROVIDERS = ["groq", "openrouter", "gemini", "ollama"]


class AISettingsWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config_path: Path | None = None

        self.provider_input = QComboBox()
        self.provider_input.addItems(LLM_PROVIDERS)
        self.host_input = QLineEdit()
        self.model_input = QLineEdit()
        self.api_key_env_input = QLineEdit()
        self.temperature_input = QDoubleSpinBox()
        self.temperature_input.setRange(0.0, 2.0)
        self.temperature_input.setSingleStep(0.1)
        self.top_p_input = QDoubleSpinBox()
        self.top_p_input.setRange(0.0, 1.0)
        self.top_p_input.setSingleStep(0.05)
        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(1, 600)
        self.retries_input = QSpinBox()
        self.retries_input.setRange(0, 10)

        self.save_button = QPushButton("Сохранить")
        self.save_button.clicked.connect(self._on_save)

        form = QFormLayout()
        form.addRow("Провайдер", self.provider_input)
        form.addRow("Модель", self.model_input)
        form.addRow("Хост Ollama (только provider=ollama)", self.host_input)
        form.addRow("Переменная окружения с API-ключом (только provider=gemini)", self.api_key_env_input)
        form.addRow("Temperature", self.temperature_input)
        form.addRow("Top-p", self.top_p_input)
        form.addRow("Таймаут (сек)", self.timeout_input)
        form.addRow("Повторных попыток", self.retries_input)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.save_button)
        self.setLayout(layout)

    def bind_config(self, config: AppConfig, config_path: Path) -> None:
        self._config_path = config_path
        llm = config.llm
        self.provider_input.setCurrentText(llm.provider)
        self.host_input.setText(llm.host)
        self.model_input.setText(llm.model)
        self.api_key_env_input.setText(llm.api_key_env)
        self.temperature_input.setValue(llm.temperature)
        self.top_p_input.setValue(llm.top_p)
        self.timeout_input.setValue(llm.timeout_seconds)
        self.retries_input.setValue(llm.retries)

    def _on_save(self) -> None:
        if self._config_path is None:
            return
        try:
            update_config_section(
                self._config_path,
                "llm",
                provider=self.provider_input.currentText(),
                host=self.host_input.text(),
                model=self.model_input.text(),
                api_key_env=self.api_key_env_input.text(),
                temperature=self.temperature_input.value(),
                top_p=self.top_p_input.value(),
                timeout_seconds=self.timeout_input.value(),
                retries=self.retries_input.value(),
            )
        except ConfigValidationError as error:
            QMessageBox.warning(self, "Некорректные настройки", str(error))
            return
        QMessageBox.information(self, "Готово", "Настройки ИИ сохранены")


class PublishingSettingsWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config_path: Path | None = None
        self._telegram_token_env = "TG_BOT_TOKEN"
        self._vk_token_env = "VK_GROUP_TOKEN"

        self.telegram_enabled_input = QCheckBox("Публикация в Telegram включена")
        self.telegram_chat_id_input = QLineEdit()
        self.vk_enabled_input = QCheckBox("Публикация в VK включена")
        self.vk_group_id_input = QSpinBox()
        self.vk_group_id_input.setRange(0, 999_999_999)

        self.save_button = QPushButton("Сохранить")
        self.save_button.clicked.connect(self._on_save)

        form = QFormLayout()
        form.addRow("", self.telegram_enabled_input)
        form.addRow("Telegram chat_id/канал", self.telegram_chat_id_input)
        form.addRow("", self.vk_enabled_input)
        form.addRow("VK group_id", self.vk_group_id_input)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.save_button)
        self.setLayout(layout)

    def bind_config(self, config: AppConfig, config_path: Path) -> None:
        self._config_path = config_path
        telegram = config.publishing.telegram
        vk = config.publishing.vk
        self._telegram_token_env = telegram.token_env
        self._vk_token_env = vk.token_env

        self.telegram_enabled_input.setChecked(telegram.enabled)
        self.telegram_chat_id_input.setText(telegram.destination)
        self.vk_enabled_input.setChecked(vk.enabled)
        self.vk_group_id_input.setValue(int(vk.destination))

    def _on_save(self) -> None:
        if self._config_path is None:
            return
        try:
            update_config_section(
                self._config_path,
                "publishing",
                targets={
                    "telegram": {
                        "enabled": self.telegram_enabled_input.isChecked(),
                        "bot_token_env": self._telegram_token_env,
                        "chat_id": self.telegram_chat_id_input.text(),
                    },
                    "vk": {
                        "enabled": self.vk_enabled_input.isChecked(),
                        "token_env": self._vk_token_env,
                        "group_id": self.vk_group_id_input.value(),
                    },
                },
            )
        except ConfigValidationError as error:
            QMessageBox.warning(self, "Некорректные настройки", str(error))
            return
        QMessageBox.information(self, "Готово", "Настройки публикации сохранены")


class LogsSettingsWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config_path: Path | None = None

        self.level_input = QComboBox()
        self.level_input.addItems(LOG_LEVELS)
        self.max_file_size_input = QSpinBox()
        self.max_file_size_input.setRange(1, 1000)
        self.backup_count_input = QSpinBox()
        self.backup_count_input.setRange(1, 100)

        self.save_button = QPushButton("Сохранить")
        self.save_button.clicked.connect(self._on_save)

        form = QFormLayout()
        form.addRow("Уровень логирования", self.level_input)
        form.addRow("Макс. размер файла (МБ)", self.max_file_size_input)
        form.addRow("Число бэкапов", self.backup_count_input)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.save_button)
        self.setLayout(layout)

    def bind_config(self, config: AppConfig, config_path: Path) -> None:
        self._config_path = config_path
        self.level_input.setCurrentText(config.logging.level)
        self.max_file_size_input.setValue(config.logging.max_file_size_mb)
        self.backup_count_input.setValue(config.logging.backup_count)

    def _on_save(self) -> None:
        if self._config_path is None:
            return
        try:
            update_config_section(
                self._config_path,
                "logging",
                level=self.level_input.currentText(),
                max_file_size_mb=self.max_file_size_input.value(),
                backup_count=self.backup_count_input.value(),
            )
        except ConfigValidationError as error:
            QMessageBox.warning(self, "Некорректные настройки", str(error))
            return
        QMessageBox.information(self, "Готово", "Настройки логов сохранены")


class SettingsPage(QTabWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.general_tab = GeneralSettingsWidget()
        self.filters_tab = FiltersSettingsWidget()
        self.ai_tab = AISettingsWidget()
        self.publishing_tab = PublishingSettingsWidget()
        self.logs_tab = LogsSettingsWidget()

        self.addTab(self.general_tab, "Общие")
        self.addTab(self.filters_tab, "Фильтры")
        self.addTab(self.ai_tab, "ИИ")
        self.addTab(self.publishing_tab, "Публикация")
        self.addTab(self.logs_tab, "Логи")

    def bind_config(self, config: AppConfig, config_path: Path) -> None:
        self.general_tab.bind_config(config, config_path)
        self.filters_tab.bind_config(config, config_path)
        self.ai_tab.bind_config(config, config_path)
        self.publishing_tab.bind_config(config, config_path)
        self.logs_tab.bind_config(config, config_path)
