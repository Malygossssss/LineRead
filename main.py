"""Application entry point for the desktop single-line reader."""

from __future__ import annotations

import sys
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Any, Mapping

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from config import CONFIG_PATH, load_config, save_config
from reader_window import DesktopReader
from single_instance import SingleInstanceGuard
from weread_source import WeReadChapter, WeReadController, WeReadError, WeReadSource


STARTUP_CONNECTING_TEXT = "正在连接微信读书…"
STARTUP_LOGIN_TEXT = "等待登录…"
STARTUP_BOOK_TEXT = "等待选书…"
STARTUP_RENDERING_TEXT = "文本渲染中…"
INSTANCE_KEY = "LineRead-" + sha256(
    str(Path(__file__).resolve()).casefold().encode("utf-8")
).hexdigest()[:16]


class WeReadIntegration(QObject):
    """Bridge browser operations to source-agnostic reader callbacks."""

    startup_status = Signal(str)
    startup_ready = Signal(object)
    startup_failed = Signal(str)
    chapter_ready = Signal(object)
    chapter_failed = Signal(str)

    def __init__(
        self,
        controller: WeReadController | None = None,
        *,
        poll_interval_seconds: float = 0.5,
        startup_timeout_seconds: float | None = None,
    ) -> None:
        super().__init__()
        self._controller = controller
        self._source: WeReadSource | None = None
        self._poll_interval_seconds = max(0.0, poll_interval_seconds)
        self._startup_timeout_seconds = (
            max(1.0, startup_timeout_seconds)
            if startup_timeout_seconds is not None
            else None
        )
        self._executor: ThreadPoolExecutor | None = None
        self._startup_future: Future[Any] | None = None
        self._chapter_future: Future[Any] | None = None
        self._stop_event = Event()

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
        """Recapture the current book without showing a login confirmation dialog."""

        try:
            saved = deepcopy(saved_state) if isinstance(saved_state, Mapping) else None
            return self._run_sync(lambda: self._open_worker(saved))
        except WeReadError as exc:
            QMessageBox.warning(parent, "微信读书连接失败", str(exc))
            return None

    def start(self, saved_state: Mapping[str, Any] | None = None) -> None:
        """Begin non-blocking login, book-selection, and chapter rendering."""

        if self._startup_future is not None and not self._startup_future.done():
            return
        saved = deepcopy(saved_state) if isinstance(saved_state, Mapping) else None
        self._stop_event.clear()
        self._startup_future = self._ensure_executor().submit(
            self._startup_worker,
            saved,
        )

    def switch_book(
        self,
        parent: QWidget | None,
        saved_state: Mapping[str, Any] | None,
    ) -> WeReadChapter | None:
        """Give the browser to the user, then capture and resume the chosen book."""

        try:
            self._run_sync(self._prepare_book_switch)
            QMessageBox.information(
                parent,
                "切换微信读书书籍",
                "请在微信读书浏览器中手动选择并进入新书。\n\n"
                "完成后回到此提示并点击“确定”，LineRead 将从保存位置继续。",
            )
            saved = deepcopy(saved_state) if isinstance(saved_state, Mapping) else None
            return self._run_sync(lambda: self._wait_and_load_worker(saved))
        except WeReadError as exc:
            QMessageBox.warning(parent, "切换书籍失败", str(exc))
            return None

    def change_chapter(
        self,
        parent: QWidget | None,
        direction: int,
    ) -> bool:
        """Queue adjacent chapter rendering and return without blocking Qt."""

        if direction == 1:
            return self._submit_chapter_change(self.source.next_chapter)
        if direction == -1:
            return self._submit_chapter_change(self.source.previous_chapter)
        return False

    def select_chapter(
        self,
        parent: QWidget | None,
        chapter_id: str,
    ) -> bool:
        """Queue direct rendering of one stable catalog chapter id."""

        if not isinstance(chapter_id, str) or not chapter_id:
            return False
        return self._submit_chapter_change(
            lambda: self.source.select_chapter(chapter_id)
        )

    def close(self) -> None:
        self._stop_event.set()
        executor = self._executor
        if executor is None:
            self._close_worker()
            return

        worker_running = (
            self._startup_future is not None and not self._startup_future.done()
        ) or (self._chapter_future is not None and not self._chapter_future.done())
        try:
            close_future = executor.submit(self._close_worker)
            if not worker_running:
                close_future.result(timeout=5)
        except Exception:
            pass
        finally:
            executor.shutdown(wait=False, cancel_futures=False)
            self._executor = None

    def _startup_worker(self, saved_state: Mapping[str, Any] | None) -> None:
        try:
            self.startup_status.emit(STARTUP_CONNECTING_TEXT)
            self.source.connect()
            self.source.restore_window()
            deadline = (
                monotonic() + self._startup_timeout_seconds
                if self._startup_timeout_seconds is not None
                else None
            )
            last_status = ""
            while not self._stop_event.is_set():
                readiness = self.controller.readiness_state()
                if readiness == "reader":
                    break
                status = (
                    STARTUP_LOGIN_TEXT if readiness == "login" else STARTUP_BOOK_TEXT
                )
                if status != last_status:
                    self.startup_status.emit(status)
                    last_status = status
                if deadline is not None and monotonic() >= deadline:
                    raise WeReadError("等待登录或选择书籍超时，请重新启动后再试。")
                self._stop_event.wait(self._poll_interval_seconds)

            if self._stop_event.is_set():
                return
            self.startup_status.emit(STARTUP_RENDERING_TEXT)
            chapter = self.source.load_current_chapter()
            chapter = self._restore_saved_chapter(chapter, saved_state)
            if not self._stop_event.is_set():
                self.startup_ready.emit(chapter)
        except Exception as exc:
            if not self._stop_event.is_set():
                self.startup_failed.emit(_error_message(exc))

    def _open_worker(
        self,
        saved_state: Mapping[str, Any] | None,
    ) -> WeReadChapter:
        self.source.connect()
        self.source.restore_window()
        if self.controller.readiness_state() != "reader":
            self.controller.wait_for_reader()
        chapter = self.source.load_current_chapter()
        return self._restore_saved_chapter(chapter, saved_state)

    def _prepare_book_switch(self) -> None:
        self.source.connect()
        self.source.restore_window()

    def _wait_and_load_worker(
        self,
        saved_state: Mapping[str, Any] | None,
    ) -> WeReadChapter:
        self.controller.wait_for_reader()
        chapter = self.source.load_current_chapter()
        return self._restore_saved_chapter(chapter, saved_state)

    def _ensure_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="LineRead-WeRead",
            )
        return self._executor

    def _submit_chapter_change(
        self,
        callback: Callable[[], WeReadChapter],
    ) -> bool:
        if self._stop_event.is_set():
            return False
        if self._startup_future is not None and not self._startup_future.done():
            return False
        if self._chapter_future is not None and not self._chapter_future.done():
            return False
        self._chapter_future = self._ensure_executor().submit(
            self._chapter_worker,
            callback,
        )
        return True

    def _chapter_worker(self, callback: Callable[[], WeReadChapter]) -> None:
        try:
            chapter = callback()
            if not self._stop_event.is_set():
                self.chapter_ready.emit(chapter)
        except Exception as exc:
            if not self._stop_event.is_set():
                self.chapter_failed.emit(_error_message(exc))

    def _run_sync(self, callback: Callable[[], Any]) -> Any:
        if self._executor is None:
            return callback()
        return self._executor.submit(callback).result()

    def _close_worker(self) -> None:
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

    instance_guard = SingleInstanceGuard(INSTANCE_KEY)
    try:
        if not instance_guard.start():
            return 0
    except RuntimeError as exc:
        QMessageBox.critical(None, "LineRead 启动失败", str(exc))
        return 1

    try:
        state = load_config(CONFIG_PATH)
        integration = WeReadIntegration()
        reader = DesktopReader(
            [STARTUP_CONNECTING_TEXT],
            state,
            file_path="",
            save_callback=lambda current: save_config(current, CONFIG_PATH),
            source_type="weread",
            open_weread_callback=integration.open,
            switch_book_callback=integration.switch_book,
            chapter_change_callback=integration.change_chapter,
            chapter_select_callback=integration.select_chapter,
            shutdown_callback=integration.close,
        )
        reader.set_loading_status(STARTUP_CONNECTING_TEXT)
        integration.startup_status.connect(reader.set_loading_status)
        integration.startup_ready.connect(reader.load_weread_chapter)
        integration.startup_failed.connect(reader.show_loading_error)
        integration.chapter_ready.connect(reader.finish_chapter_change)
        integration.chapter_failed.connect(reader.fail_chapter_change)
        instance_guard.activation_requested.connect(reader.restore_and_activate)
        reader.show()
        integration.start(state.get("weread"))
        return app.exec()
    finally:
        instance_guard.close()


def _error_message(error: BaseException) -> str:
    text = str(error).strip()
    return text.splitlines()[0] if text else type(error).__name__


if __name__ == "__main__":
    raise SystemExit(main())
