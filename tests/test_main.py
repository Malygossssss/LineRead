import os
import sys
import unittest
from threading import get_ident
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QSignalSpy

from main import WeReadIntegration, main
from weread_source import WeReadChapter, WeReadError


class StartupTests(unittest.TestCase):
    def test_starts_reader_immediately_and_begins_async_weread_connection(self):
        state = {
            "source": "txt",
            "file": "D:/books/old.txt",
        }
        app = Mock()
        app.exec.return_value = 17

        with (
            patch.object(sys, "argv", ["main.py", "D:/books/ignored.txt"]),
            patch("main.QApplication", return_value=app),
            patch("main.SingleInstanceGuard") as guard_type,
            patch("main.load_config", return_value=state),
            patch("main.WeReadIntegration") as integration_type,
            patch("main.DesktopReader") as reader_type,
        ):
            guard_type.return_value.start.return_value = True
            integration = integration_type.return_value
            reader = reader_type.return_value

            result = main()

        self.assertEqual(result, 17)
        integration.open.assert_not_called()
        integration.start.assert_called_once_with()
        self.assertEqual(reader_type.call_args.args[0], ["正在连接微信读书…"])
        self.assertEqual(reader_type.call_args.kwargs["source_type"], "weread")
        self.assertEqual(
            reader_type.call_args.kwargs["page_change_callback"],
            integration.change_page,
        )
        self.assertNotIn("open_file_callback", reader_type.call_args.kwargs)
        reader.set_loading_status.assert_called_once_with("正在连接微信读书…")
        reader.show.assert_called_once()
        guard_type.return_value.activation_requested.connect.assert_called_once_with(
            reader.restore_and_activate
        )
        guard_type.return_value.close.assert_called_once_with()

    def test_second_launch_notifies_existing_instance_and_exits(self):
        app = Mock()

        with (
            patch("main.QApplication", return_value=app),
            patch("main.SingleInstanceGuard") as guard_type,
            patch("main.DesktopReader") as reader_type,
        ):
            guard_type.return_value.start.return_value = False

            result = main()

        self.assertEqual(result, 0)
        reader_type.assert_not_called()
        app.exec.assert_not_called()


class FakeWeReadController:
    def __init__(self, *, reader_page=True, readiness_states=None, error=None):
        self.reader_page = reader_page
        self.readiness_states = list(readiness_states or [])
        self.error = error
        self.calls = []
        self.thread_ids = []

    def _record(self, name):
        self.calls.append(name)
        self.thread_ids.append(get_ident())

    def connect(self):
        self._record("connect")

    def restore_window(self):
        self._record("restore")

    def is_reader_page(self):
        return self.reader_page

    def readiness_state(self):
        self._record("readiness")
        if self.readiness_states:
            state = self.readiness_states.pop(0)
            self.reader_page = state == "reader"
            return state
        return "reader" if self.reader_page else "book"

    def wait_for_reader(self):
        self._record("wait")
        self.reader_page = True

    def get_current_chapter(self):
        self._record("current")
        if self.error is not None:
            raise self.error
        return {
            "book_id": "book-1",
            "book_title": "测试书",
            "chapter_id": "chapter-1",
            "chapter_title": "第一章",
            "chapter_url": "https://weread.qq.com/web/reader/book-1-chapter-1",
            "paragraphs": ["正文。"],
        }

    def next_chapter(self):
        self._record("next")

    def previous_chapter(self):
        self._record("previous")

    def next_page(self):
        self._record("next_page")

    def previous_page(self):
        self._record("previous_page")

    def select_chapter(self, chapter_id):
        self._record(("select", chapter_id))

    def close(self):
        self._record("close")


class PagingFakeWeReadController(FakeWeReadController):
    """A small linear book used to exercise speculative page movement."""

    def __init__(self):
        super().__init__()
        self.position = 0
        self.pages = {
            -1: "上一页。",
            0: "当前页。",
            1: "下一页。",
            2: "下下页。",
        }

    def get_current_chapter(self):
        self._record("current")
        return {
            "book_id": "book-1",
            "book_title": "测试书",
            "chapter_id": "chapter-1",
            "chapter_title": "第一章",
            "chapter_url": "https://weread.qq.com/web/reader/book-1-chapter-1",
            "paragraphs": [self.pages[self.position]],
        }

    def next_page(self):
        self._record("next_page")
        if self.position >= max(self.pages):
            raise WeReadError("当前已经是最后一页。")
        self.position += 1

    def previous_page(self):
        self._record("previous_page")
        if self.position <= min(self.pages):
            raise WeReadError("当前已经是第一页。")
        self.position -= 1


class WeReadIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_open_waits_without_showing_a_login_confirmation_dialog(self):
        controller = FakeWeReadController(reader_page=False)
        integration = WeReadIntegration(controller)
        self.addCleanup(integration.close)

        with patch("main.QMessageBox.information") as information:
            chapter = integration.open(None)

        self.assertIsInstance(chapter, WeReadChapter)
        information.assert_not_called()
        self.assertIn("wait", controller.calls)

    def test_async_startup_emits_login_book_rendering_and_ready_states(self):
        controller = FakeWeReadController(
            reader_page=False,
            readiness_states=["login", "book", "reader"],
        )
        integration = WeReadIntegration(controller, poll_interval_seconds=0)
        self.addCleanup(integration.close)
        status_spy = QSignalSpy(integration.startup_status)
        ready_spy = QSignalSpy(integration.startup_ready)

        integration.start()

        integration._startup_future.result(timeout=1)
        self.app.processEvents()
        self.assertEqual(ready_spy.count(), 1)
        statuses = [status_spy.at(index)[0] for index in range(status_spy.count())]
        self.assertEqual(
            statuses,
            ["正在连接微信读书…", "等待登录…", "等待选书…", "文本渲染中…"],
        )
        self.assertIsInstance(ready_spy.at(0)[0], WeReadChapter)

    def test_async_startup_reports_errors_without_a_message_box(self):
        controller = FakeWeReadController(error=WeReadError("正文读取失败"))
        integration = WeReadIntegration(controller, poll_interval_seconds=0)
        self.addCleanup(integration.close)
        failed_spy = QSignalSpy(integration.startup_failed)

        with patch("main.QMessageBox.warning") as warning:
            integration.start()
            integration._startup_future.result(timeout=1)
            self.app.processEvents()

        self.assertEqual(failed_spy.at(0)[0], "正文读取失败")
        warning.assert_not_called()

    def test_chapter_changes_stay_on_the_startup_playwright_thread(self):
        controller = FakeWeReadController()
        integration = WeReadIntegration(controller, poll_interval_seconds=0)
        self.addCleanup(integration.close)

        integration.start()
        integration._startup_future.result(timeout=1)
        ready_spy = QSignalSpy(integration.chapter_ready)

        accepted = integration.change_chapter(None, 1)
        integration._chapter_future.result(timeout=1)
        self.app.processEvents()

        self.assertTrue(accepted)
        self.assertEqual(ready_spy.count(), 1)
        self.assertIsInstance(ready_spy.at(0)[0], WeReadChapter)
        browser_thread_ids = [
            thread_id
            for call, thread_id in zip(controller.calls, controller.thread_ids)
            if call in ("connect", "restore", "readiness", "current", "next")
        ]
        self.assertEqual(len(set(browser_thread_ids)), 1)

    def test_direct_chapter_selection_runs_asynchronously(self):
        controller = FakeWeReadController()
        integration = WeReadIntegration(controller, poll_interval_seconds=0)
        self.addCleanup(integration.close)
        ready_spy = QSignalSpy(integration.chapter_ready)

        integration.start()
        integration._startup_future.result(timeout=1)
        accepted = integration.select_chapter(None, "chapter-6")
        integration._chapter_future.result(timeout=1)
        self.app.processEvents()

        self.assertTrue(accepted)
        self.assertIn(("select", "chapter-6"), controller.calls)
        self.assertEqual(ready_spy.count(), 1)

    def test_page_turn_runs_asynchronously_and_emits_direction(self):
        controller = FakeWeReadController()
        integration = WeReadIntegration(controller, poll_interval_seconds=0)
        self.addCleanup(integration.close)
        integration.start()
        integration._startup_future.result(timeout=1)
        ready_spy = QSignalSpy(integration.page_ready)

        accepted = integration.change_page(None, -1)
        integration._page_future.result(timeout=1)
        self.app.processEvents()

        self.assertTrue(accepted)
        self.assertIn("previous_page", controller.calls)
        self.assertEqual(ready_spy.count(), 1)
        self.assertIsInstance(ready_spy.at(0)[0], WeReadChapter)
        self.assertEqual(ready_spy.at(0)[1], -1)

    def test_prefetch_restores_browser_and_serves_both_directions_from_cache(self):
        controller = PagingFakeWeReadController()
        integration = WeReadIntegration(controller, poll_interval_seconds=0)
        self.addCleanup(integration.close)
        self.assertIsNotNone(integration.open(None))
        ready_spy = QSignalSpy(integration.page_ready)

        integration._prefetch_future.result(timeout=1)

        self.assertEqual(controller.position, 0)
        self.assertEqual(controller.calls[-3:], ["next_page", "current", "previous_page"])

        self.assertTrue(integration.change_page(None, 1))
        self.assertEqual(ready_spy.count(), 1)
        self.assertEqual(ready_spy.at(0)[0].units, ("下一页。",))
        integration._page_future.result(timeout=1)
        self.assertEqual(controller.position, 1)

        self.assertTrue(integration.change_page(None, -1))
        self.assertEqual(ready_spy.count(), 2)
        self.assertEqual(ready_spy.at(1)[0].units, ("当前页。",))
        integration._page_future.result(timeout=1)
        self.assertEqual(controller.position, 0)

    def test_page_cache_keeps_only_five_nearest_snapshots(self):
        integration = WeReadIntegration(FakeWeReadController())
        self.addCleanup(integration.close)
        chapter = WeReadChapter(
            book_id="book-1",
            book_title="测试书",
            chapter_id="chapter-1",
            chapter_title="第一章",
            chapter_url="https://weread.qq.com/web/reader/book-1",
            units=("正文。",),
        )
        with integration._page_state_lock:
            integration._current_page_index = 4
            integration._page_cache = {index: chapter for index in range(9)}
            integration._prune_page_cache_locked()

        self.assertEqual(len(integration._page_cache), 5)
        self.assertIn(4, integration._page_cache)

    def test_page_turn_error_is_emitted_without_worker_message_box(self):
        controller = FakeWeReadController(error=WeReadError("页面读取失败"))
        integration = WeReadIntegration(controller, poll_interval_seconds=0)
        self.addCleanup(integration.close)
        failed_spy = QSignalSpy(integration.page_failed)

        with patch("main.QMessageBox.warning") as warning:
            self.assertTrue(integration.change_page(None, 1))
            integration._page_future.result(timeout=1)
            self.app.processEvents()

        self.assertEqual(failed_spy.at(0)[0], "页面读取失败")
        warning.assert_not_called()

    def test_async_chapter_error_is_emitted_without_worker_message_box(self):
        controller = FakeWeReadController(error=WeReadError("正文读取失败"))
        integration = WeReadIntegration(controller, poll_interval_seconds=0)
        self.addCleanup(integration.close)
        failed_spy = QSignalSpy(integration.chapter_failed)

        with patch("main.QMessageBox.warning") as warning:
            self.assertTrue(integration.change_chapter(None, 1))
            integration._chapter_future.result(timeout=1)
            self.app.processEvents()

        self.assertEqual(failed_spy.at(0)[0], "正文读取失败")
        warning.assert_not_called()

    def test_open_uses_the_current_browser_chapter_without_saved_state(self):
        controller = FakeWeReadController()
        integration = WeReadIntegration(controller)
        self.addCleanup(integration.close)

        chapter = integration.open(None)

        self.assertIsNotNone(chapter)
        self.assertNotIn("open_url", controller.calls)
        self.assertEqual(chapter.chapter_id, "chapter-1")


if __name__ == "__main__":
    unittest.main()
