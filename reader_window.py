"""Always-on-top single-line reader window."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import (
    QCloseEvent,
    QContextMenuEvent,
    QEnterEvent,
    QFont,
    QMouseEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QDialog, QLabel, QMenu, QVBoxLayout, QWidget

from config import (
    ALLOWED_WHEEL_MODIFIERS,
    DEFAULT_SHORTCUTS,
    MAX_FONT_SIZE,
    MAX_VISIBLE_OPACITY,
    MIN_FONT_SIZE,
    MIN_VISIBLE_OPACITY,
)
from settings_dialog import SettingsDialog


HIDDEN_OPACITY = 0.05

_QT_MODIFIERS = {
    "Ctrl": Qt.KeyboardModifier.ControlModifier,
    "Shift": Qt.KeyboardModifier.ShiftModifier,
    "Alt": Qt.KeyboardModifier.AltModifier,
}


class DesktopReader(QWidget):
    """Display and navigate an in-memory sequence of reading units."""

    def __init__(
        self,
        units: Sequence[str],
        state: Mapping[str, Any],
        *,
        file_path: str = "",
        save_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__()

        self.units = [self._single_line(unit) for unit in units if unit.strip()]
        if not self.units:
            raise ValueError("没有可显示的阅读内容。")

        self.file_path = file_path
        self.save_callback = save_callback
        self.index = max(0, min(int(state.get("index", 0)), len(self.units) - 1))
        self.font_size = max(
            MIN_FONT_SIZE,
            min(MAX_FONT_SIZE, int(state.get("font_size", 14))),
        )
        self.visible_opacity = max(
            MIN_VISIBLE_OPACITY,
            min(MAX_VISIBLE_OPACITY, float(state.get("opacity", 0.85))),
        )
        shortcut_state = state.get("shortcuts", {})
        if not isinstance(shortcut_state, Mapping):
            shortcut_state = {}
        self.font_wheel_modifier = self._valid_modifier(
            shortcut_state.get("font_wheel"), DEFAULT_SHORTCUTS["font_wheel"]
        )
        self.opacity_wheel_modifier = self._valid_modifier(
            shortcut_state.get("opacity_wheel"), DEFAULT_SHORTCUTS["opacity_wheel"]
        )
        if self.opacity_wheel_modifier == self.font_wheel_modifier:
            self.opacity_wheel_modifier = (
                "Shift" if self.font_wheel_modifier != "Shift" else "Ctrl"
            )
        self._hovered = False
        self._settings_open = False
        self._drag_offset: QPoint | None = None

        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # Qt tool windows do not always quit the application when closed unless
        # this attribute is enabled explicitly.
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, True)
        self.setMouseTracking(True)

        self.label = QLabel(self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setWordWrap(False)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.label.setStyleSheet(
            "QLabel {"
            "  color: #F3F4F6;"
            "  background-color: rgba(28, 30, 34, 220);"
            "  border: 1px solid rgba(255, 255, 255, 28);"
            "  border-radius: 10px;"
            "  padding: 0 18px;"
            "}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)

        width = max(400, min(2400, int(state.get("width", 900))))
        self.setFixedWidth(width)
        self.move(int(state.get("x", 400)), int(state.get("y", 50)))
        self._apply_font()
        self._show_current_unit()
        self.setWindowOpacity(HIDDEN_OPACITY)

    def navigate(self, amount: int) -> None:
        """Move by ``amount`` units while respecting both boundaries."""

        new_index = max(0, min(self.index + amount, len(self.units) - 1))
        if new_index != self.index:
            self.index = new_index
            self._show_current_unit()

    def adjust_font_size(self, amount: int) -> None:
        new_size = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, self.font_size + amount))
        if new_size != self.font_size:
            self.font_size = new_size
            self._apply_font()

    def adjust_visible_opacity(self, amount: float) -> None:
        new_opacity = max(
            MIN_VISIBLE_OPACITY,
            min(MAX_VISIBLE_OPACITY, self.visible_opacity + amount),
        )
        self.visible_opacity = round(new_opacity, 2)
        if self._hovered:
            self.setWindowOpacity(self.visible_opacity)

    def get_state(self, file_path: str | None = None) -> dict[str, Any]:
        position = self.pos()
        return {
            "file": self.file_path if file_path is None else file_path,
            "index": self.index,
            "x": position.x(),
            "y": position.y(),
            "width": self.width(),
            "font_size": self.font_size,
            "opacity": self.visible_opacity,
            "shortcuts": {
                "font_wheel": self.font_wheel_modifier,
                "opacity_wheel": self.opacity_wheel_modifier,
            },
        }

    def apply_settings(self, settings: Mapping[str, Any], *, persist: bool = True) -> None:
        """Apply validated dialog values and optionally save them immediately."""

        font_size = int(settings.get("font_size", self.font_size))
        self.font_size = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, font_size))
        opacity = float(settings.get("opacity", self.visible_opacity))
        self.visible_opacity = round(
            max(MIN_VISIBLE_OPACITY, min(MAX_VISIBLE_OPACITY, opacity)), 2
        )

        shortcuts = settings.get("shortcuts", {})
        if isinstance(shortcuts, Mapping):
            font_modifier = self._valid_modifier(
                shortcuts.get("font_wheel"), self.font_wheel_modifier
            )
            opacity_modifier = self._valid_modifier(
                shortcuts.get("opacity_wheel"), self.opacity_wheel_modifier
            )
            if font_modifier != opacity_modifier:
                self.font_wheel_modifier = font_modifier
                self.opacity_wheel_modifier = opacity_modifier

        self._apply_font()
        if self._hovered or self._settings_open:
            self.setWindowOpacity(self.visible_opacity)
        if persist:
            self._persist_state()

    def open_settings(self) -> None:
        """Open the modal settings dialog from the reader context menu."""

        dialog = SettingsDialog(self.get_state(), self)
        self._settings_open = True
        self.setWindowOpacity(self.visible_opacity)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.apply_settings(dialog.get_settings())
        finally:
            self._settings_open = False
            if not self.underMouse():
                self.setWindowOpacity(HIDDEN_OPACITY)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 (Qt API)
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta == 0:
            event.ignore()
            return

        direction = 1 if delta > 0 else -1
        modifiers = event.modifiers()
        if modifiers == _QT_MODIFIERS[self.font_wheel_modifier]:
            self.adjust_font_size(direction)
        elif modifiers == _QT_MODIFIERS[self.opacity_wheel_modifier]:
            self.adjust_visible_opacity(direction * 0.05)
        elif modifiers == Qt.KeyboardModifier.NoModifier:
            self.navigate(-direction)
        event.accept()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802
        menu = QMenu(self)
        settings_action = menu.addAction("设置…")
        menu.addSeparator()
        exit_action = menu.addAction("退出")
        selected = menu.exec(event.globalPos())
        if selected is settings_action:
            self.open_settings()
        elif selected is exit_action:
            self.close()
        event.accept()

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802 (Qt API)
        self._hovered = True
        self.setWindowOpacity(self.visible_opacity)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self._hovered = False
        if not self._settings_open:
            self.setWindowOpacity(HIDDEN_OPACITY)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt API)
        self._persist_state()
        event.accept()

    def _show_current_unit(self) -> None:
        self.label.setText(self.units[self.index])

    def _apply_font(self) -> None:
        font = QFont("Microsoft YaHei")
        font.setPointSize(self.font_size)
        self.label.setFont(font)
        height = self.label.fontMetrics().lineSpacing() + 22
        self.setFixedHeight(max(38, height))

    def _persist_state(self) -> None:
        if self.save_callback is not None:
            try:
                self.save_callback(self.get_state())
            except OSError as exc:
                print(f"保存阅读进度失败：{exc}")

    @staticmethod
    def _valid_modifier(value: Any, fallback: str) -> str:
        return value if value in ALLOWED_WHEEL_MODIFIERS else fallback

    @staticmethod
    def _single_line(unit: str) -> str:
        return " ".join(unit.replace("\r", "\n").splitlines()).strip()
