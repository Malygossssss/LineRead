"""Lightweight local interaction settings for the desktop reader."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from config import (
    ALLOWED_WHEEL_MODIFIERS,
    DEFAULT_SHORTCUTS,
    MAX_FONT_SIZE,
    MAX_VISIBLE_OPACITY,
    MIN_FONT_SIZE,
    MIN_VISIBLE_OPACITY,
)


class SettingsDialog(QDialog):
    """Edit reader appearance and modifier-plus-wheel bindings."""

    def __init__(self, state: Mapping[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("单行阅读设置")
        self.setModal(True)
        self.setMinimumWidth(460)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        title = QLabel("阅读设置")
        title.setObjectName("titleLabel")
        root.addWidget(title)

        instructions = QLabel(
            "操作说明\n"
            "• 普通滚轮：上一条 / 下一条\n"
            "• 修饰键 + 滚轮：调整字号或透明度\n"
            "• 按住左键：拖动悬浮窗\n"
            "• 右键：打开此设置页\n"
            "• Alt + F4：退出并保存"
        )
        instructions.setWordWrap(True)
        instructions.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        instructions.setObjectName("instructionsLabel")
        root.addWidget(instructions)

        appearance_group = QGroupBox("显示")
        appearance_form = QFormLayout(appearance_group)
        appearance_form.setHorizontalSpacing(18)
        appearance_form.setVerticalSpacing(10)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(MIN_FONT_SIZE, MAX_FONT_SIZE)
        self.font_size_spin.setValue(int(state.get("font_size", 14)))
        self.font_size_spin.setSuffix(" pt")
        appearance_form.addRow("字号", self.font_size_spin)

        self.opacity_spin = QDoubleSpinBox()
        self.opacity_spin.setRange(MIN_VISIBLE_OPACITY, MAX_VISIBLE_OPACITY)
        self.opacity_spin.setSingleStep(0.05)
        self.opacity_spin.setDecimals(2)
        self.opacity_spin.setValue(float(state.get("opacity", 0.85)))
        appearance_form.addRow("鼠标移入透明度", self.opacity_spin)
        root.addWidget(appearance_group)

        shortcut_group = QGroupBox("滚轮快捷操作")
        shortcut_form = QFormLayout(shortcut_group)
        shortcut_form.setHorizontalSpacing(18)
        shortcut_form.setVerticalSpacing(10)

        shortcuts = state.get("shortcuts", {})
        if not isinstance(shortcuts, Mapping):
            shortcuts = {}
        self.font_modifier_combo = self._modifier_combo(
            shortcuts.get("font_wheel", DEFAULT_SHORTCUTS["font_wheel"])
        )
        self.opacity_modifier_combo = self._modifier_combo(
            shortcuts.get("opacity_wheel", DEFAULT_SHORTCUTS["opacity_wheel"])
        )
        shortcut_form.addRow("调整字号", self.font_modifier_combo)
        shortcut_form.addRow("调整透明度", self.opacity_modifier_combo)
        root.addWidget(shortcut_group)

        self.error_label = QLabel("")
        self.error_label.setObjectName("errorLabel")
        self.error_label.setVisible(False)
        root.addWidget(self.error_label)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setObjectName("divider")
        root.addWidget(divider)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept_if_valid)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.font_modifier_combo.currentIndexChanged.connect(self._clear_error)
        self.opacity_modifier_combo.currentIndexChanged.connect(self._clear_error)
        self.setStyleSheet(
            "QDialog { background: #202226; color: #F3F4F6; }"
            "QLabel, QGroupBox { color: #F3F4F6; }"
            "QLabel#titleLabel { font-size: 18px; font-weight: 600; }"
            "QLabel#instructionsLabel { color: #C9CDD4; line-height: 1.4; }"
            "QLabel#errorLabel { color: #FF8A8A; }"
            "QGroupBox { border: 1px solid #3A3D43; border-radius: 8px;"
            " margin-top: 8px; padding: 12px 10px 10px 10px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }"
            "QSpinBox, QDoubleSpinBox, QComboBox { background: #2A2D32; color: #F3F4F6;"
            " border: 1px solid #484C54; border-radius: 5px; padding: 5px 8px;"
            " min-width: 150px; }"
            "QPushButton { min-width: 72px; padding: 6px 14px; border-radius: 5px;"
            " border: 1px solid #50545C; background: #30333A; color: #F3F4F6; }"
            "QPushButton:hover { background: #3A3E46; }"
            "QFrame#divider { color: #3A3D43; }"
        )

    def get_settings(self) -> dict[str, Any]:
        return {
            "font_size": self.font_size_spin.value(),
            "opacity": round(self.opacity_spin.value(), 2),
            "shortcuts": {
                "font_wheel": self.font_modifier_combo.currentData(),
                "opacity_wheel": self.opacity_modifier_combo.currentData(),
            },
        }

    def accept_if_valid(self) -> None:
        if self.font_modifier_combo.currentData() == self.opacity_modifier_combo.currentData():
            self.error_label.setText("两个滚轮快捷操作的修饰键不能相同。")
            self.error_label.setVisible(True)
            return
        self.accept()

    def _modifier_combo(self, selected: Any) -> QComboBox:
        combo = QComboBox()
        for modifier in ALLOWED_WHEEL_MODIFIERS:
            combo.addItem(f"{modifier} + 滚轮", modifier)
        selected_index = combo.findData(selected)
        combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
        return combo

    def _clear_error(self) -> None:
        self.error_label.setVisible(False)
