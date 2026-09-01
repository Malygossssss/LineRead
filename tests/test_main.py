import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from main import WeReadIntegration, select_txt_file
from weread_source import WeReadChapter, WeReadError


class SelectTxtFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_returns_absolute_path_and_parsed_units(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "book.txt"
            path.write_text("第一条。第二条！", encoding="utf-8")

            with patch(
                "main.QFileDialog.getOpenFileName",
                return_value=(str(path), "Text files (*.txt)"),
            ):
                result = select_txt_file(None, "")

            self.assertEqual(result, (str(path.resolve()), ["第一条。", "第二条！"]))

    def test_cancel_returns_none(self):
        with patch("main.QFileDialog.getOpenFileName", return_value=("", "")):
            self.assertIsNone(select_txt_file(None, "D:/books/current.txt"))

    def test_invalid_file_shows_error_and_allows_cancel(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "empty.txt"
            path.write_text("\n\n", encoding="utf-8")

            with (
                patch(
                    "main.QFileDialog.getOpenFileName",
                    side_effect=[(str(path), "Text files (*.txt)"), ("", "")],
                ),
                patch("main.QMessageBox.warning") as warning,
            ):
                result = select_txt_file(None, "")

            self.assertIsNone(result)
            warning.assert_called_once()
            self.assertIn("没有可阅读内容", warning.call_args.args[2])


class FakeWeReadController:
    def __init__(self, *, reader_page=True):
        self.reader_page = reader_page
        self.calls = []

    def connect(self):
        self.calls.append("connect")

    def restore_window(self):
        self.calls.append("restore")

    def is_reader_page(self):
        return self.reader_page

    def wait_for_reader(self):
        self.calls.append("wait")
        self.reader_page = True

    def get_current_chapter(self):
        self.calls.append("current")
        return {
            "book_id": "book-1",
            "book_title": "测试书",
            "chapter_id": "chapter-1",
            "chapter_title": "第一章",
            "chapter_url": "https://weread.qq.com/web/reader/book-1-chapter-1",
            "paragraphs": ["正文。"],
        }

    def open_chapter_url(self, url, chapter_id=""):
        self.calls.append(("open_url", url, chapter_id))

    def close(self):
        self.calls.append("close")


class WeReadIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_first_connection_prompts_and_waits_for_reader_page(self):
        controller = FakeWeReadController(reader_page=False)
        integration = WeReadIntegration(controller)

        with patch("main.QMessageBox.information") as information:
            chapter = integration.open(None)

        self.assertIsInstance(chapter, WeReadChapter)
        information.assert_called_once()
        self.assertIn("wait", controller.calls)

    def test_saved_chapter_url_is_restored_for_selected_book(self):
        controller = FakeWeReadController()
        integration = WeReadIntegration(controller)
        state = {
            "books": {
                "book-1": {
                    "chapter_id": "chapter-9",
                    "chapter_url": "https://weread.qq.com/web/reader/book-1-chapter-9",
                }
            }
        }

        chapter = integration.open(None, state)

        self.assertIsNotNone(chapter)
        self.assertIn(
            (
                "open_url",
                "https://weread.qq.com/web/reader/book-1-chapter-9",
                "chapter-9",
            ),
            controller.calls,
        )


if __name__ == "__main__":
    unittest.main()
