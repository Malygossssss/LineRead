"""Always-on-top single-line reader window."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
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
from PySide6.QtWidgets import QDialog, QLabel, QMenu, QMessageBox, QVBoxLayout, QWidget

from chapter_selection_dialog import ChapterSelectionDialog
from config import (
    ALLOWED_WHEEL_MODIFIERS,
    DEFAULT_SHORTCUTS,
    MAX_FONT_SIZE,
    MAX_VISIBLE_OPACITY,
    MIN_FONT_SIZE,
    MIN_VISIBLE_OPACITY,
    normalize_config,
)
from reading_details_dialog import ReadingDetailsDialog
from settings_dialog import SettingsDialog
from weread_source import WeReadCatalogEntry, WeReadChapter


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
        open_file_callback: (
            Callable[[QWidget, str], tuple[str, Sequence[str]] | None] | None
        ) = None,
        source_type: str | None = None,
        weread_chapter: WeReadChapter | None = None,
        open_weread_callback: (
            Callable[[QWidget, Mapping[str, Any] | None], WeReadChapter | None] | None
        ) = None,
        switch_book_callback: (
            Callable[[QWidget, Mapping[str, Any] | None], WeReadChapter | None] | None
        ) = None,
        chapter_change_callback: (
            Callable[[QWidget, int], bool] | None
        ) = None,
        chapter_select_callback: (
            Callable[[QWidget, str], bool] | None
        ) = None,
        shutdown_callback: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()

        normalized_state = normalize_config(state)
        self.units = self._normalize_units(units)
        if not self.units:
            raise ValueError("没有可显示的阅读内容。")

        self.file_path = file_path
        self.save_callback = save_callback
        self.open_file_callback = open_file_callback
        self.open_weread_callback = open_weread_callback
        self.switch_book_callback = switch_book_callback
        self.chapter_change_callback = chapter_change_callback
        self.chapter_select_callback = chapter_select_callback
        self.shutdown_callback = shutdown_callback
        requested_source = source_type or normalized_state.get("source", "txt")
        self.source_type = requested_source if requested_source in ("txt", "weread") else "txt"
        self.weread_state = deepcopy(normalized_state["weread"])
        self.book_id = ""
        self.book_title = ""
        self.chapter_id = ""
        self.chapter_title = ""
        self.chapter_url = ""
        self.chapter_catalog: tuple[WeReadCatalogEntry, ...] = ()
        self.chapter_catalog_index = -1
        if weread_chapter is not None:
            self._set_weread_metadata(weread_chapter)
            self.source_type = "weread"
        elif self.source_type == "weread":
            self._load_saved_weread_metadata()

        initial_index = normalized_state.get("index", 0)
        if self.source_type == "weread" and self.book_id:
            initial_index = self._saved_line_index(self.book_id, self.chapter_id)
        self.index = max(0, min(int(initial_index), len(self.units) - 1))
        self.font_size = max(
            MIN_FONT_SIZE,
            min(MAX_FONT_SIZE, int(normalized_state.get("font_size", 14))),
        )
        self.visible_opacity = max(
            MIN_VISIBLE_OPACITY,
            min(MAX_VISIBLE_OPACITY, float(normalized_state.get("opacity", 0.85))),
        )
        shortcut_state = normalized_state.get("shortcuts", {})
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
        self._dialog_open = False
        self._drag_offset: QPoint | None = None
        self._loading = False
        self._loading_text = ""

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

        width = max(400, min(2400, int(normalized_state.get("width", 900))))
        self.setFixedWidth(width)
        self.move(int(normalized_state.get("x", 400)), int(normalized_state.get("y", 50)))
        self._apply_font()
        self._show_current_unit()
        self.setWindowOpacity(HIDDEN_OPACITY)

    def navigate(self, amount: int) -> None:
        """Move by ``amount`` units while respecting both boundaries."""

        if self._loading:
            return
        if (
            amount > 0
            and self.source_type == "weread"
            and self.index + amount >= len(self.units)
        ):
            self.change_chapter(1)
            return
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
        self._remember_current_weread_position()
        position = self.pos()
        return {
            "source": self.source_type,
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
            "weread": deepcopy(self.weread_state),
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
        if self._hovered or self._dialog_open:
            self.setWindowOpacity(self.visible_opacity)
        if persist:
            self._persist_state()

    def open_settings(self) -> None:
        """Open the modal settings dialog from the reader context menu."""

        dialog = SettingsDialog(self.get_state(), self)
        self._dialog_open = True
        self.setWindowOpacity(self.visible_opacity)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.apply_settings(dialog.get_settings())
        finally:
            self._dialog_open = False
            if not self.underMouse():
                self.setWindowOpacity(HIDDEN_OPACITY)

    def open_text_file(self) -> None:
        """Ask the injected source callback for new units and display them."""

        if self.open_file_callback is None:
            return
        self._dialog_open = True
        self.setWindowOpacity(self.visible_opacity)
        try:
            result = self.open_file_callback(self, self.file_path)
            if result is not None:
                file_path, units = result
                self.replace_content(units, file_path)
        finally:
            self._dialog_open = False
            if not self.underMouse():
                self.setWindowOpacity(HIDDEN_OPACITY)

    def set_loading_status(self, status: str) -> None:
        """Show a transient WeRead startup state without persisting progress."""

        text = self._single_line(status) or "正在连接微信读书…"
        self._loading = True
        self._loading_text = text
        self.units = [text]
        self.index = 0
        self._show_current_unit()

    def show_loading_error(self, message: str) -> None:
        """Keep startup failures visible in the floating reader."""

        detail = self._single_line(message) or "未知错误"
        self.set_loading_status(f"连接失败：{detail}")

    def load_weread_chapter(self, chapter: WeReadChapter) -> None:
        """Replace startup state text with a ready chapter and restore progress."""

        self._loading = False
        self._loading_text = ""
        self._apply_weread_chapter(chapter, restore=True)

    def replace_content(
        self,
        units: Sequence[str],
        file_path: str,
        *,
        persist: bool = True,
    ) -> None:
        """Replace source-agnostic content and restart at the first unit."""

        normalized_units = self._normalize_units(units)
        if not normalized_units:
            raise ValueError("没有可显示的阅读内容。")
        self.units = normalized_units
        self._remember_current_weread_position()
        self.file_path = file_path
        self.source_type = "txt"
        self.index = 0
        self._show_current_unit()
        if persist:
            self._persist_state()

    def open_weread(self) -> None:
        """Connect to or recapture the current WeRead book."""

        if self._loading or self.open_weread_callback is None:
            return
        self._remember_current_weread_position()
        saved = deepcopy(self.weread_state)
        self._run_weread_callback(
            lambda: self.open_weread_callback(self, saved),
            restore=True,
        )

    def switch_weread_book(self) -> None:
        """Let the user choose a book in Chromium and then recapture it."""

        if self._loading or self.switch_book_callback is None:
            return
        self._remember_current_weread_position()
        saved = deepcopy(self.weread_state)
        self._run_weread_callback(
            lambda: self.switch_book_callback(self, saved),
            restore=True,
        )

    def change_chapter(self, direction: int) -> None:
        """Request an adjacent chapter without blocking the Qt event loop."""

        if (
            self._loading
            or self.chapter_change_callback is None
            or direction not in (-1, 1)
        ):
            return
        self._begin_chapter_change()
        if not self.chapter_change_callback(self, direction):
            self._cancel_chapter_change()

    def choose_chapter(self) -> None:
        """Open the cached catalog and request the chosen stable chapter id."""

        if (
            self._loading
            or self.chapter_select_callback is None
            or not self.chapter_catalog
        ):
            return
        dialog = ChapterSelectionDialog(
            self.chapter_catalog,
            self.chapter_id,
            self,
        )
        self._dialog_open = True
        self.setWindowOpacity(self.visible_opacity)
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            chapter_id = dialog.selected_chapter_id() if accepted else ""
        finally:
            self._dialog_open = False
        if chapter_id and chapter_id != self.chapter_id:
            self._begin_chapter_change()
            if not self.chapter_select_callback(self, chapter_id):
                self._cancel_chapter_change()
        elif not self.underMouse():
            self.setWindowOpacity(HIDDEN_OPACITY)

    def finish_chapter_change(self, chapter: WeReadChapter) -> None:
        """Apply a chapter completed by the Playwright worker."""

        self._loading = False
        self._loading_text = ""
        self._apply_weread_chapter(chapter, restore=False)
        if not self.underMouse():
            self.setWindowOpacity(HIDDEN_OPACITY)

    def fail_chapter_change(self, message: str) -> None:
        """Restore the cached line after a background chapter failure."""

        self._cancel_chapter_change()
        QMessageBox.warning(self, "章节切换失败", message or "未知错误")

    def show_reading_details(self) -> None:
        dialog = ReadingDetailsDialog(self.reading_detail_lines(), self)
        self._dialog_open = True
        self.setWindowOpacity(self.visible_opacity)
        try:
            dialog.exec()
        finally:
            self._dialog_open = False
            if not self.underMouse():
                self.setWindowOpacity(HIDDEN_OPACITY)

    def reading_detail_lines(self) -> list[str]:
        if self._loading:
            return [
                "当前来源：微信读书",
                f"当前状态：{self._loading_text or self.label.text()}",
            ]
        if self.source_type == "weread":
            return [
                "当前来源：微信读书",
                f"当前书籍：《{self.book_title or '未知书籍'}》",
                f"当前章节：{self.chapter_title or '未知章节'}",
                f"当前章节进度：{self.index + 1} / {len(self.units)} 行",
            ]
        return [
            "当前来源：TXT",
            f"当前文件：{self.file_path or '未命名文本'}",
            f"当前进度：{self.index + 1} / {len(self.units)} 行",
        ]

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
        menu = self.create_context_menu()
        menu.exec(event.globalPos())
        event.accept()

    def create_context_menu(self) -> QMenu:
        """Build the reader's right-click menu for display or testing."""

        menu = QMenu(self)
        if self.source_type == "weread":
            open_weread_action = menu.addAction("打开微信读书")
            open_weread_action.setEnabled(
                not self._loading and self.open_weread_callback is not None
            )
            open_weread_action.triggered.connect(self.open_weread)
            switch_action = menu.addAction("切换书籍")
            switch_action.setEnabled(
                not self._loading and self.switch_book_callback is not None
            )
            switch_action.triggered.connect(self.switch_weread_book)
            menu.addSeparator()
            choose_action = menu.addAction("选择章节…")
            choose_action.setEnabled(
                not self._loading
                and self.chapter_select_callback is not None
                and bool(self.chapter_catalog)
            )
            choose_action.triggered.connect(self.choose_chapter)
            previous_action = menu.addAction("上一章")
            previous_action.setEnabled(
                not self._loading and self.chapter_change_callback is not None
            )
            previous_action.triggered.connect(lambda: self.change_chapter(-1))
            next_action = menu.addAction("下一章")
            next_action.setEnabled(
                not self._loading and self.chapter_change_callback is not None
            )
            next_action.triggered.connect(lambda: self.change_chapter(1))
        else:
            open_action = menu.addAction("打开文件")
            open_action.setEnabled(self.open_file_callback is not None)
            open_action.triggered.connect(self.open_text_file)
        menu.addSeparator()
        details_action = menu.addAction("阅读详情")
        details_action.triggered.connect(self.show_reading_details)
        settings_action = menu.addAction("设置")
        settings_action.triggered.connect(self.open_settings)
        menu.addSeparator()
        exit_action = menu.addAction("退出")
        exit_action.triggered.connect(self.close)
        return menu

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802 (Qt API)
        self._hovered = True
        self.setWindowOpacity(self.visible_opacity)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self._hovered = False
        if not self._dialog_open:
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
        if self.shutdown_callback is not None:
            try:
                self.shutdown_callback()
            except Exception as exc:
                print(f"关闭微信读书浏览器失败：{exc}")
        event.accept()

    def _run_weread_callback(
        self,
        callback: Callable[[], WeReadChapter | None],
        *,
        restore: bool,
    ) -> None:
        self._remember_current_weread_position()
        self._dialog_open = True
        self.setWindowOpacity(self.visible_opacity)
        try:
            chapter = callback()
            if chapter is not None:
                self._apply_weread_chapter(chapter, restore=restore)
        finally:
            self._dialog_open = False
            if not self.underMouse():
                self.setWindowOpacity(HIDDEN_OPACITY)

    def _apply_weread_chapter(self, chapter: WeReadChapter, *, restore: bool) -> None:
        units = self._normalize_units(chapter.units)
        if not units:
            raise ValueError("当前章节没有可显示的阅读内容。")
        self.units = units
        self._set_weread_metadata(chapter)
        self.source_type = "weread"
        self.weread_state["active_book_id"] = chapter.book_id
        index = self._saved_line_index(chapter.book_id, chapter.chapter_id) if restore else 0
        self.index = max(0, min(index, len(self.units) - 1))
        self._show_current_unit()
        self._persist_state()

    def _set_weread_metadata(self, chapter: WeReadChapter) -> None:
        self.book_id = chapter.book_id
        self.book_title = chapter.book_title
        self.chapter_id = chapter.chapter_id
        self.chapter_title = chapter.chapter_title
        self.chapter_url = chapter.chapter_url
        self.chapter_catalog = chapter.catalog
        self.chapter_catalog_index = chapter.catalog_index

    def _begin_chapter_change(self) -> None:
        self._remember_current_weread_position()
        self._loading = True
        self._loading_text = "文本渲染中…"
        self.label.setText(self._loading_text)
        self.setWindowOpacity(self.visible_opacity)

    def _cancel_chapter_change(self) -> None:
        self._loading = False
        self._loading_text = ""
        self._show_current_unit()
        if not self.underMouse():
            self.setWindowOpacity(HIDDEN_OPACITY)

    def _load_saved_weread_metadata(self) -> None:
        active_id = self.weread_state.get("active_book_id", "")
        books = self.weread_state.get("books", {})
        position = books.get(active_id, {}) if isinstance(books, Mapping) else {}
        if isinstance(position, Mapping):
            self.book_id = str(position.get("book_id", ""))
            self.book_title = str(position.get("book_title", ""))
            self.chapter_id = str(position.get("chapter_id", ""))
            self.chapter_title = str(position.get("chapter_title", ""))
            self.chapter_url = str(position.get("chapter_url", ""))

    def _saved_line_index(self, book_id: str, chapter_id: str) -> int:
        books = self.weread_state.get("books", {})
        position = books.get(book_id, {}) if isinstance(books, Mapping) else {}
        if not isinstance(position, Mapping) or position.get("chapter_id") != chapter_id:
            return 0
        value = position.get("line_index", 0)
        return max(0, value) if isinstance(value, int) and not isinstance(value, bool) else 0

    def _current_weread_position(self) -> dict[str, Any] | None:
        if self.source_type != "weread" or not self.book_id or not self.chapter_id:
            return None
        return {
            "book_id": self.book_id,
            "book_title": self.book_title,
            "chapter_id": self.chapter_id,
            "chapter_title": self.chapter_title,
            "chapter_url": self.chapter_url,
            "line_index": self.index,
        }

    def _remember_current_weread_position(self) -> None:
        if self._loading:
            return
        position = self._current_weread_position()
        if position is None:
            return
        books = self.weread_state.setdefault("books", {})
        books[position["book_id"]] = position
        self.weread_state["active_book_id"] = position["book_id"]

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

    @classmethod
    def _normalize_units(cls, units: Sequence[str]) -> list[str]:
        return [cls._single_line(unit) for unit in units if unit.strip()]

    @staticmethod
    def _single_line(unit: str) -> str:
        return " ".join(unit.replace("\r", "\n").splitlines()).strip()
