"""Application entry point for the desktop single-line reader."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Mapping

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QWidget

from config import CONFIG_PATH, load_config, save_config
from reader_window import DesktopReader
from text_parser import TxtSource
from weread_source import WeReadChapter, WeReadController, WeReadError, WeReadSource


class WeReadIntegration:
    """Bridge browser operations to source-agnostic reader callbacks."""

    def __init__(self, controller: WeReadController | None = None) -> None:
        self._controller = controller
        self._source: WeReadSource | None = None

    @property
    def controller(self) -> WeReadController:
        if self._controller is None:
            self._controller = WeReadController()
        return self._controller

    @property
    def source(self) -> WeReadSource:
        if self._source is None:
            self._source = WeReadSource(self.controller)
        return self._source

    def open(
        self,
        parent: QWidget | None,
        saved_state: Mapping[str, Any] | None = None,
    ) -> WeReadChapter | None:
        """Connect, prompt for first login when needed, and capture a chapter."""

        try:
            self.source.connect()
            self.source.restore_window()
            if not self.controller.is_reader_page():
                QMessageBox.information(
                    parent,
                    "连接微信读书",
                    "请在已打开的微信读书浏览器中扫码登录并进入一本书。\n\n"
                    "完成后回到此提示并点击“确定”，LineRead 将读取当前章节。",
                )
                self.controller.wait_for_reader()
            chapter = self.source.load_current_chapter()
            return self._restore_saved_chapter(chapter, saved_state)
        except WeReadError as exc:
            QMessageBox.warning(parent, "微信读书连接失败", str(exc))
            return None

    def switch_book(
        self,
        parent: QWidget | None,
        saved_state: Mapping[str, Any] | None,
    ) -> WeReadChapter | None:
        """Give the browser to the user, then capture and resume the chosen book."""

        try:
            self.source.connect()
            self.source.restore_window()
            QMessageBox.information(
                parent,
                "切换微信读书书籍",
                "请在微信读书浏览器中手动选择并进入新书。\n\n"
                "完成后回到此提示并点击“确定”，LineRead 将从保存位置继续。",
            )
            self.controller.wait_for_reader()
            chapter = self.source.load_current_chapter()
            return self._restore_saved_chapter(chapter, saved_state)
        except WeReadError as exc:
            QMessageBox.warning(parent, "切换书籍失败", str(exc))
            return None

    def change_chapter(
        self,
        parent: QWidget | None,
        direction: int,
    ) -> WeReadChapter | None:
        try:
            if direction == 1:
                return self.source.next_chapter()
            if direction == -1:
                return self.source.previous_chapter()
            return None
        except WeReadError as exc:
            QMessageBox.warning(parent, "章节切换失败", str(exc))
            return None

    def close(self) -> None:
        if self._source is not None:
            self._source.close()
        elif self._controller is not None:
            self._controller.close()

    def _restore_saved_chapter(
        self,
        chapter: WeReadChapter,
        saved_state: Mapping[str, Any] | None,
    ) -> WeReadChapter:
        if not isinstance(saved_state, Mapping):
            return chapter
        books = saved_state.get("books")
        if not isinstance(books, Mapping):
            return chapter
        position = books.get(chapter.book_id)
        if not isinstance(position, Mapping):
            return chapter
        saved_id = position.get("chapter_id")
        saved_url = position.get("chapter_url")
        if (
            isinstance(saved_id, str)
            and saved_id
            and saved_id != chapter.chapter_id
            and isinstance(saved_url, str)
            and saved_url
        ):
            return self.source.restore_chapter(saved_url, saved_id)
        return chapter


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("单行阅读")
    app.setQuitOnLastWindowClosed(True)

    state = load_config(CONFIG_PATH)
    arguments = sys.argv[1:]
    use_weread = "--weread" in arguments or (
        not arguments and state.get("source") == "weread"
    )
    requested_path = next((arg for arg in arguments if not arg.startswith("--")), "")
    if not requested_path and not use_weread:
        requested_path = state.get("file", "")
    path = Path(requested_path).expanduser() if requested_path else None
    integration = WeReadIntegration()
    chapter: WeReadChapter | None = None

    if use_weread:
        chapter = integration.open(None, state.get("weread"))

    if chapter is not None:
        units = list(chapter.units)
        absolute_path = state.get("file", "")
        source_type = "weread"
    else:
        if path is None and isinstance(state.get("file"), str) and state.get("file"):
            path = Path(state["file"]).expanduser()
        while True:
            if path is None or not path.is_file():
                if path is not None:
                    QMessageBox.warning(None, "文件不存在", f"找不到 TXT 文件：\n{path}")
                selected, _ = QFileDialog.getOpenFileName(
                    None,
                    "选择 UTF-8 TXT 文件",
                    str(path.parent) if path is not None else "",
                    "Text files (*.txt)",
                )
                if not selected:
                    integration.close()
                    return 0
                path = Path(selected)

            try:
                units = TxtSource(path).get_units()
                break
            except (OSError, ValueError) as exc:
                QMessageBox.warning(None, "无法打开 TXT", str(exc))
                path = None

        absolute_path = str(path.resolve())
        source_type = "txt"
        if not _same_file(state.get("file", ""), absolute_path):
            state["index"] = 0

    reader = DesktopReader(
        units,
        state,
        file_path=absolute_path,
        save_callback=lambda current: save_config(current, CONFIG_PATH),
        open_file_callback=select_txt_file,
        source_type=source_type,
        weread_chapter=chapter,
        open_weread_callback=integration.open,
        switch_book_callback=integration.switch_book,
        chapter_change_callback=integration.change_chapter,
        shutdown_callback=integration.close,
    )
    reader.show()
    return app.exec()


def select_txt_file(
    parent: QWidget | None,
    current_file: str,
) -> tuple[str, list[str]] | None:
    """Choose and load a UTF-8 TXT, retrying after readable load errors."""

    initial_directory = ""
    if current_file:
        initial_directory = str(Path(current_file).expanduser().parent)

    while True:
        selected, _ = QFileDialog.getOpenFileName(
            parent,
            "打开 UTF-8 TXT 文件",
            initial_directory,
            "Text files (*.txt)",
        )
        if not selected:
            return None

        path = Path(selected)
        try:
            units = TxtSource(path).get_units()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(parent, "无法打开 TXT", str(exc))
            initial_directory = str(path.parent)
            continue
        return str(path.resolve()), units


def _same_file(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


if __name__ == "__main__":
    raise SystemExit(main())
