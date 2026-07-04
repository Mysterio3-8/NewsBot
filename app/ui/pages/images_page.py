"""Изображения — приоритет провайдеров + watermark (разделы 12.1-12.3 SPEC.md)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.config.loader import AppConfig, ConfigValidationError, update_config_section

ALL_PROVIDERS = ["source", "unsplash", "pexels", "pixabay", "local_ai"]
ASPECT_RATIOS = ["1:1", "4:5", "16:9", "9:16"]
WATERMARK_POSITIONS = ["bottom-right", "bottom-left", "top-right", "top-left"]


class ImagesPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config_path: Path | None = None

        self.provider_list = QListWidget()
        for name in ALL_PROVIDERS:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.provider_list.addItem(item)
        self.move_up_button = QPushButton("▲ Выше приоритет")
        self.move_down_button = QPushButton("▼ Ниже приоритет")
        self.move_up_button.clicked.connect(lambda: self._move_selected(-1))
        self.move_down_button.clicked.connect(lambda: self._move_selected(1))

        self.count_per_post_input = QSpinBox()
        self.count_per_post_input.setRange(1, 10)
        self.aspect_ratio_input = QComboBox()
        self.aspect_ratio_input.addItems(ASPECT_RATIOS)

        self.logo_path_input = QLineEdit()
        self.position_input = QComboBox()
        self.position_input.addItems(WATERMARK_POSITIONS)
        self.opacity_input = QSpinBox()
        self.opacity_input.setRange(0, 100)
        self.margin_input = QSpinBox()
        self.margin_input.setRange(0, 200)
        self.size_ratio_input = QSpinBox()
        self.size_ratio_input.setRange(1, 100)
        self.size_ratio_input.setSuffix("% от ширины изображения")
        self.channel_name_text_input = QCheckBox("Показывать название канала на изображении")

        self.save_button = QPushButton("Сохранить")
        self.save_button.clicked.connect(self._on_save)

        provider_controls = QHBoxLayout()
        provider_controls.addWidget(self.move_up_button)
        provider_controls.addWidget(self.move_down_button)

        form = QFormLayout()
        form.addRow("Провайдеров на пост", self.count_per_post_input)
        form.addRow("Соотношение сторон", self.aspect_ratio_input)
        form.addRow("Путь к логотипу", self.logo_path_input)
        form.addRow("Позиция watermark", self.position_input)
        form.addRow("Прозрачность (%)", self.opacity_input)
        form.addRow("Отступ (px)", self.margin_input)
        form.addRow("Размер логотипа", self.size_ratio_input)
        form.addRow("", self.channel_name_text_input)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Провайдеры изображений (галочка = включён, порядок = приоритет)"))
        layout.addWidget(self.provider_list)
        layout.addLayout(provider_controls)
        layout.addLayout(form)
        layout.addWidget(self.save_button)
        self.setLayout(layout)

    def _move_selected(self, offset: int) -> None:
        row = self.provider_list.currentRow()
        new_row = row + offset
        if row < 0 or not (0 <= new_row < self.provider_list.count()):
            return
        item = self.provider_list.takeItem(row)
        self.provider_list.insertItem(new_row, item)
        self.provider_list.setCurrentRow(new_row)

    def bind_config(self, config: AppConfig, config_path: Path) -> None:
        self._config_path = config_path

        self.provider_list.clear()
        ordered = list(config.images.providers_order)
        remaining = [p for p in ALL_PROVIDERS if p not in ordered]
        for name in ordered + remaining:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = name in config.images.providers_order
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self.provider_list.addItem(item)

        self.count_per_post_input.setValue(config.images.count_per_post)
        self.aspect_ratio_input.setCurrentText(config.images.target_aspect_ratio)

        self.logo_path_input.setText(config.watermark.logo_path)
        self.position_input.setCurrentText(config.watermark.position)
        self.opacity_input.setValue(config.watermark.opacity)
        self.margin_input.setValue(config.watermark.margin_px)
        self.size_ratio_input.setValue(round(config.watermark.size_ratio * 100))
        self.channel_name_text_input.setChecked(config.watermark.channel_name_text)

    def _current_providers_order(self) -> list[str]:
        order = []
        for row in range(self.provider_list.count()):
            item = self.provider_list.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                order.append(item.text())
        return order

    def _on_save(self) -> None:
        if self._config_path is None:
            return
        try:
            update_config_section(
                self._config_path,
                "images",
                providers_order=self._current_providers_order(),
                count_per_post=self.count_per_post_input.value(),
                target_aspect_ratio=self.aspect_ratio_input.currentText(),
            )
            update_config_section(
                self._config_path,
                "watermark",
                logo_path=self.logo_path_input.text(),
                position=self.position_input.currentText(),
                opacity=self.opacity_input.value(),
                margin_px=self.margin_input.value(),
                size_ratio=self.size_ratio_input.value() / 100,
                channel_name_text=self.channel_name_text_input.isChecked(),
            )
        except ConfigValidationError as error:
            QMessageBox.warning(self, "Некорректные настройки", str(error))
            return
        QMessageBox.information(self, "Готово", "Настройки изображений сохранены")
