"""Планировщик публикации (раздел 13.2 SPEC.md)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.config.loader import AppConfig, ConfigValidationError, update_config_section

SCHEDULE_MODES = ["fixed_slots", "interval"]


def parse_slots(text: str) -> list[str]:
    slots = [line.strip() for line in text.splitlines() if line.strip()]
    for slot in slots:
        parts = slot.split(":")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            raise ValueError(f"Неверный формат времени: {slot!r} (ожидается ЧЧ:ММ)")
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"Неверное время: {slot!r}")
    return slots


class SchedulerPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config_path: Path | None = None

        self.mode_input = QComboBox()
        self.mode_input.addItems(SCHEDULE_MODES)
        self.fixed_slots_input = QTextEdit()
        self.interval_minutes_input = QSpinBox()
        self.interval_minutes_input.setRange(1, 1440)
        self.max_posts_per_day_input = QSpinBox()
        self.max_posts_per_day_input.setRange(0, 100)
        self.min_interval_minutes_input = QSpinBox()
        self.min_interval_minutes_input.setRange(0, 1440)
        self.jitter_minutes_input = QSpinBox()
        self.jitter_minutes_input.setRange(0, 120)
        self.important_score_threshold_input = QSpinBox()
        self.important_score_threshold_input.setRange(0, 100)
        self.include_hashtags_input = QCheckBox("Публиковать хэштеги (LLM их генерирует в любом случае)")

        self.save_button = QPushButton("Сохранить")
        self.save_button.clicked.connect(self._on_save)

        form = QFormLayout()
        form.addRow("Режим", self.mode_input)
        form.addRow("Слоты (ЧЧ:ММ, по одному в строке)", self.fixed_slots_input)
        form.addRow("Интервал (мин, режим interval)", self.interval_minutes_input)
        form.addRow("Постов в сутки", self.max_posts_per_day_input)
        form.addRow("Мин. интервал между публикациями (мин, антиспам)", self.min_interval_minutes_input)
        form.addRow("Случайный разброс времени (± мин)", self.jitter_minutes_input)
        form.addRow("Порог «главных» постов (score ≥)", self.important_score_threshold_input)
        form.addRow("", self.include_hashtags_input)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.save_button)
        self.setLayout(layout)

    def bind_config(self, config: AppConfig, config_path: Path) -> None:
        self._config_path = config_path
        schedule = config.publishing.schedule
        self.mode_input.setCurrentText(schedule.mode)
        self.fixed_slots_input.setPlainText("\n".join(schedule.fixed_slots))
        self.interval_minutes_input.setValue(schedule.interval_minutes)
        self.max_posts_per_day_input.setValue(schedule.max_posts_per_day)
        self.min_interval_minutes_input.setValue(schedule.min_interval_minutes)
        self.jitter_minutes_input.setValue(schedule.jitter_minutes)
        self.important_score_threshold_input.setValue(config.filters.important_score_threshold)
        self.include_hashtags_input.setChecked(config.rewrite.include_hashtags)

    def _on_save(self) -> None:
        if self._config_path is None:
            return
        try:
            fixed_slots = parse_slots(self.fixed_slots_input.toPlainText())
        except ValueError as error:
            QMessageBox.warning(self, "Некорректное расписание", str(error))
            return

        try:
            update_config_section(
                self._config_path,
                "publishing",
                schedule={
                    "mode": self.mode_input.currentText(),
                    "fixed_slots": fixed_slots,
                    "interval_minutes": self.interval_minutes_input.value(),
                    "max_posts_per_day": self.max_posts_per_day_input.value(),
                    "min_interval_minutes": self.min_interval_minutes_input.value(),
                    "jitter_minutes": self.jitter_minutes_input.value(),
                },
            )
            update_config_section(
                self._config_path,
                "filters",
                important_score_threshold=self.important_score_threshold_input.value(),
            )
            update_config_section(
                self._config_path,
                "rewrite",
                include_hashtags=self.include_hashtags_input.isChecked(),
            )
        except ConfigValidationError as error:
            QMessageBox.warning(self, "Некорректные настройки", str(error))
            return
        QMessageBox.information(self, "Готово", "Расписание сохранено")
