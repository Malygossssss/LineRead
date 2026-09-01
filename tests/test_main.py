import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from main import select_txt_file


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


if __name__ == "__main__":
    unittest.main()
