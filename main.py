"""Application entry point for the desktop single-line reader."""

from __future__ import annotations

import sys
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from threading import Event, RLock
from time import monotonic
from typing import Any

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
PAGE_CACHE_LIMIT = 5
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
    page_ready = Signal(object, int)
    page_failed = Signal(str)

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
        self._page_future: Future[Any] | None = None
        self._prefetch_future: Future[Any] | None = None
        self._stop_event = Event()
        self._page_state_lock = RLock()
        self._page_cache: dict[int, WeReadChapter] = {}
        self._page_cache_generation = 0
        self._current_page_index: int | None = None
        self._browser_page_index: int | None = None
        self._prefetch_attempted: set[tuple[int, int]] = set()

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
    ) -> WeReadChapter | None:
        """Recapture the chapter currently open in the WeRead browser."""

        try:
            chapter = self._run_sync(self._open_worker)
            self.prime_page_cache(chapter)
            return chapter
        except WeReadError as exc:
            QMessageBox.warning(parent, "微信读书连接失败", str(exc))
            return None

    def start(self) -> None:
        """Begin non-blocking login, book-selection, and chapter rendering."""

        if self._startup_future is not None and not self._startup_future.done():
            return
        self._stop_event.clear()
        self._startup_future = self._ensure_executor().submit(self._startup_worker)

    def switch_book(
        self,
        parent: QWidget | None,
    ) -> WeReadChapter | None:
        """Give the browser to the user, then capture its selected chapter."""

        try:
            self._run_sync(self._prepare_book_switch)
            QMessageBox.information(
                parent,
                "切换微信读书书籍",
                "请在微信读书浏览器中手动选择并进入新书。\n\n"
                "完成后回到此提示并点击“确定”，LineRead 将从当前页面第一行开始。",
            )
            chapter = self._run_sync(self._wait_and_load_worker)
            self.prime_page_cache(chapter)
            return chapter
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

    def change_page(
        self,
        parent: QWidget | None,
        direction: int,
    ) -> bool:
        """Queue one browser page turn and return without blocking Qt."""

        if direction == 1:
            return self._submit_page_change(self.source.next_page, direction)
        if direction == -1:
            return self._submit_page_change(self.source.previous_page, direction)
        return False

    def prime_page_cache(self, chapter: WeReadChapter) -> None:
        """Start a fresh in-memory page window from one displayed snapshot."""

        if not isinstance(chapter, WeReadChapter) or self._stop_event.is_set():
            return
        with self._page_state_lock:
            self._page_cache_generation += 1
            self._page_cache = {0: chapter}
            self._current_page_index = 0
            self._browser_page_index = 0
            self._prefetch_attempted.clear()
        self._schedule_page_prefetch()

    def close(self) -> None:
        self._stop_event.set()
        executor = self._executor
        if executor is None:
            self._close_worker()
            return

        worker_running = (
            self._startup_future is not None and not self._startup_future.done()
        ) or (
            self._chapter_future is not None and not self._chapter_future.done()
        ) or (
            self._page_future is not None and not self._page_future.done()
        ) or (
            self._prefetch_future is not None
            and not self._prefetch_future.done()
        )
        try:
            close_future = executor.submit(self._close_worker)
            if not worker_running:
                close_future.result(timeout=5)
        except Exception:
            pass
        finally:
            executor.shutdown(wait=False, cancel_futures=False)
            self._executor = None

    def _startup_worker(self) -> None:
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
            if not self._stop_event.is_set():
                self.startup_ready.emit(chapter)
        except Exception as exc:
            if not self._stop_event.is_set():
                self.startup_failed.emit(_error_message(exc))

    def _open_worker(self) -> WeReadChapter:
        self.source.connect()
        self.source.restore_window()
        if self.controller.readiness_state() != "reader":
            self.controller.wait_for_reader()
        return self.source.load_current_chapter()

    def _prepare_book_switch(self) -> None:
        self.source.connect()
        self.source.restore_window()

    def _wait_and_load_worker(self) -> WeReadChapter:
        self.controller.wait_for_reader()
        return self.source.load_current_chapter()

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
        if self._page_future is not None and not self._page_future.done():
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

    def _submit_page_change(
        self,
        callback: Callable[[], WeReadChapter],
        direction: int,
    ) -> bool:
        if self._stop_event.is_set():
            return False
        if self._startup_future is not None and not self._startup_future.done():
            return False
        if self._chapter_future is not None and not self._chapter_future.done():
            return False
        if self._page_future is not None and not self._page_future.done():
            return False

        cached: WeReadChapter | None = None
        target_index: int | None = None
        generation = 0
        with self._page_state_lock:
            if self._current_page_index is not None:
                target_index = self._current_page_index + direction
                cached = self._page_cache.get(target_index)
                generation = self._page_cache_generation
                if cached is not None:
                    self._current_page_index = target_index
                    self._prune_page_cache_locked()

        if cached is not None and target_index is not None:
            self.page_ready.emit(cached, direction)
            self._page_future = self._ensure_executor().submit(
                self._cached_page_worker,
                target_index,
                generation,
            )
            return True

        self._page_future = self._ensure_executor().submit(
            self._page_worker,
            callback,
            direction,
        )
        return True

    def _page_worker(
        self,
        callback: Callable[[], WeReadChapter],
        direction: int,
    ) -> None:
        try:
            with self._page_state_lock:
                base_index = self._current_page_index
                generation = self._page_cache_generation
                target_index = (
                    base_index + direction if base_index is not None else None
                )
                chapter = (
                    self._page_cache.get(target_index)
                    if target_index is not None
                    else None
                )

            if chapter is not None and target_index is not None:
                with self._page_state_lock:
                    if generation != self._page_cache_generation:
                        return
                    self._current_page_index = target_index
                    self._prune_page_cache_locked()
                if not self._stop_event.is_set():
                    self.page_ready.emit(chapter, direction)
                self._sync_browser_to_index(target_index, generation)
                self._schedule_page_prefetch()
                return

            if base_index is not None:
                self._sync_browser_to_index(base_index, generation)
            chapter = callback()

            if target_index is not None:
                with self._page_state_lock:
                    if generation != self._page_cache_generation:
                        return
                    self._browser_page_index = target_index
                    self._current_page_index = target_index
                    self._page_cache[target_index] = chapter
                    self._prune_page_cache_locked()
            if not self._stop_event.is_set():
                self.page_ready.emit(chapter, direction)
            if target_index is not None:
                self._schedule_page_prefetch()
        except Exception as exc:
            if not self._stop_event.is_set():
                self.page_failed.emit(_error_message(exc))

    def _cached_page_worker(self, target_index: int, generation: int) -> None:
        """Move the browser after the UI has consumed an in-memory page."""

        try:
            self._sync_browser_to_index(target_index, generation)
            with self._page_state_lock:
                chapter = self._page_cache.get(target_index)
            if chapter is not None:
                self.source.cached_chapter = chapter
            self._schedule_page_prefetch()
        except Exception:
            # The cached snapshot remains readable. A later demand operation will
            # retry synchronization from the last confirmed browser index.
            return

    def _schedule_page_prefetch(self) -> None:
        """Queue one forward capture when the current cache window needs it."""

        if self._stop_event.is_set():
            return
        with self._page_state_lock:
            if self._current_page_index is None:
                return
            generation = self._page_cache_generation
            base_index = self._current_page_index
            key = (generation, base_index)
            if base_index + 1 in self._page_cache or key in self._prefetch_attempted:
                return
            if self._prefetch_future is not None and not self._prefetch_future.done():
                return
            self._prefetch_attempted.add(key)
            future = self._ensure_executor().submit(
                self._prefetch_page_worker,
                generation,
                base_index,
            )
            self._prefetch_future = future
            future.add_done_callback(self._page_prefetch_finished)

    def _page_prefetch_finished(self, _future: Future[Any]) -> None:
        # A new chapter can be primed while an older speculative task is winding
        # down. Once the executor is free, give the new generation its turn.
        self._schedule_page_prefetch()

    def _prefetch_page_worker(self, generation: int, base_index: int) -> None:
        """Capture the next page and restore the browser before publishing it."""

        moved_forward = False
        restored = False
        chapter: WeReadChapter | None = None
        try:
            with self._page_state_lock:
                if (
                    generation != self._page_cache_generation
                    or base_index != self._current_page_index
                ):
                    return
            self._sync_browser_to_index(base_index, generation)
            self.controller.next_page()
            moved_forward = True
            with self._page_state_lock:
                if generation == self._page_cache_generation:
                    self._browser_page_index = base_index + 1
            chapter = self.source.load_current_chapter()
        except Exception:
            chapter = None
        finally:
            if moved_forward:
                try:
                    self.controller.previous_page()
                    restored = True
                    with self._page_state_lock:
                        if generation == self._page_cache_generation:
                            self._browser_page_index = base_index
                except Exception:
                    restored = False

        if chapter is None or not restored or self._stop_event.is_set():
            return
        with self._page_state_lock:
            if (
                generation != self._page_cache_generation
                or base_index != self._current_page_index
            ):
                return
            self._page_cache[base_index + 1] = chapter
            current = self._page_cache.get(base_index)
            self._prune_page_cache_locked()
        if current is not None:
            self.source.cached_chapter = current

    def _sync_browser_to_index(self, target_index: int, generation: int) -> None:
        """Move the single browser page to a known logical cache position."""

        while not self._stop_event.is_set():
            with self._page_state_lock:
                if generation != self._page_cache_generation:
                    return
                current = self._browser_page_index
            if current is None or current == target_index:
                return
            direction = 1 if target_index > current else -1
            if direction > 0:
                self.controller.next_page()
            else:
                self.controller.previous_page()
            with self._page_state_lock:
                if generation != self._page_cache_generation:
                    return
                self._browser_page_index = current + direction

    def _prune_page_cache_locked(self) -> None:
        if (
            self._current_page_index is None
            or len(self._page_cache) <= PAGE_CACHE_LIMIT
        ):
            return
        keep = set(
            sorted(
                self._page_cache,
                key=lambda index: (
                    abs(index - self._current_page_index),
                    -index,
                ),
            )[:PAGE_CACHE_LIMIT]
        )
        self._page_cache = {
            index: chapter
            for index, chapter in self._page_cache.items()
            if index in keep
        }

    def _run_sync(self, callback: Callable[[], Any]) -> Any:
        if self._executor is None:
            return callback()
        return self._executor.submit(callback).result()

    def _close_worker(self) -> None:
        if self._source is not None:
            self._source.close()
        elif self._controller is not None:
            self._controller.close()


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
            page_change_callback=integration.change_page,
            chapter_change_callback=integration.change_chapter,
            chapter_select_callback=integration.select_chapter,
            shutdown_callback=integration.close,
        )
        reader.set_loading_status(STARTUP_CONNECTING_TEXT)
        integration.startup_status.connect(reader.set_loading_status)
        integration.startup_ready.connect(reader.load_weread_chapter)
        integration.startup_ready.connect(integration.prime_page_cache)
        integration.startup_failed.connect(reader.show_loading_error)
        integration.chapter_ready.connect(reader.finish_chapter_change)
        integration.chapter_ready.connect(integration.prime_page_cache)
        integration.chapter_failed.connect(reader.fail_chapter_change)
        integration.page_ready.connect(reader.finish_page_change)
        integration.page_failed.connect(reader.fail_page_change)
        instance_guard.activation_requested.connect(reader.restore_and_activate)
        reader.show()
        integration.start()
        return app.exec()
    finally:
        instance_guard.close()


def _error_message(error: BaseException) -> str:
    text = str(error).strip()
    return text.splitlines()[0] if text else type(error).__name__


if __name__ == "__main__":
    raise SystemExit(main())
