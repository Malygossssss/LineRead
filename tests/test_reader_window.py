import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QDialog

from reader_window import DesktopReader
from weread_source import WeReadChapter


class DesktopReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_reader(self, **state_overrides):
        state = {
            "index": 1,
            "x": 100,
            "y": 80,
            "width": 900,
            "font_size": 14,
            "opacity": 0.85,
            "shortcuts": {
                "font_wheel": "Ctrl",
                "opacity_wheel": "Shift",
            },
        }
        state.update(state_overrides)
        return DesktopReader(["第一条。", "第二条。", "第三条。"], state)

    def test_uses_required_floating_window_flags(self):
        reader = self.make_reader()
        self.addCleanup(reader.close)

        flags = reader.windowFlags()
        self.assertTrue(flags & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(flags & Qt.WindowType.WindowStaysOnTopHint)
        self.assertTrue(flags & Qt.WindowType.Tool)
        self.assertTrue(reader.testAttribute(Qt.WidgetAttribute.WA_QuitOnClose))

    def test_navigation_stays_within_bounds(self):
        reader = self.make_reader(index=0)
        self.addCleanup(reader.close)

        reader.navigate(-1)
        self.assertEqual(reader.index, 0)
        reader.navigate(1)
        self.assertEqual(reader.label.text(), "第二条。")
        reader.navigate(99)
        self.assertEqual(reader.index, 2)

    def test_font_size_is_clamped_and_updates_label(self):
        reader = self.make_reader(font_size=14)
        self.addCleanup(reader.close)

        reader.adjust_font_size(100)
        self.assertEqual(reader.font_size, 40)
        self.assertEqual(reader.label.font().pointSize(), 40)
        reader.adjust_font_size(-100)
        self.assertEqual(reader.font_size, 10)

    def test_visible_opacity_is_clamped(self):
        reader = self.make_reader(opacity=0.85)
        self.addCleanup(reader.close)

        reader.adjust_visible_opacity(1.0)
        self.assertEqual(reader.visible_opacity, 1.0)
        reader.adjust_visible_opacity(-5.0)
        self.assertEqual(reader.visible_opacity, 0.2)

    def test_label_is_plain_single_line(self):
        reader = self.make_reader()
        self.addCleanup(reader.close)

        self.assertFalse(reader.label.wordWrap())
        self.assertEqual(reader.label.textFormat(), Qt.TextFormat.PlainText)
        self.assertNotIn("\n", reader.label.text())

    def test_exported_state_contains_current_reader_settings(self):
        reader = self.make_reader(index=2, width=880)
        self.addCleanup(reader.close)

        state = reader.get_state("D:/books/test.txt")

        self.assertEqual(state["file"], "D:/books/test.txt")
        self.assertEqual(state["index"], 2)
        self.assertEqual(state["width"], 880)
        self.assertEqual(state["font_size"], 14)
        self.assertEqual(state["opacity"], 0.85)
        self.assertEqual(
            state["shortcuts"],
            {"font_wheel": "Ctrl", "opacity_wheel": "Shift"},
        )

    def test_wheel_controls_navigation_font_and_opacity(self):
        reader = self.make_reader(index=1, font_size=14, opacity=0.85)
        self.addCleanup(reader.close)

        reader.wheelEvent(self._wheel_event(y=-120))
        self.assertEqual(reader.index, 2)
        reader.wheelEvent(self._wheel_event(y=120))
        self.assertEqual(reader.index, 1)

        reader.wheelEvent(
            self._wheel_event(y=120, modifiers=Qt.KeyboardModifier.ControlModifier)
        )
        self.assertEqual(reader.font_size, 15)

        # Windows may represent Shift + wheel as a horizontal delta.
        reader.wheelEvent(
            self._wheel_event(x=-120, modifiers=Qt.KeyboardModifier.ShiftModifier)
        )
        self.assertEqual(reader.visible_opacity, 0.8)

    def test_wheel_uses_saved_modifier_bindings(self):
        reader = self.make_reader(
            index=1,
            font_size=14,
            opacity=0.85,
            shortcuts={"font_wheel": "Alt", "opacity_wheel": "Ctrl"},
        )
        self.addCleanup(reader.close)

        reader.wheelEvent(
            self._wheel_event(y=120, modifiers=Qt.KeyboardModifier.AltModifier)
        )
        self.assertEqual(reader.font_size, 15)
        reader.wheelEvent(
            self._wheel_event(y=-120, modifiers=Qt.KeyboardModifier.ControlModifier)
        )
        self.assertEqual(reader.visible_opacity, 0.8)

        # An unassigned modifier should not accidentally turn pages.
        reader.wheelEvent(
            self._wheel_event(y=-120, modifiers=Qt.KeyboardModifier.ShiftModifier)
        )
        self.assertEqual(reader.index, 1)

    def test_settings_are_applied_and_persisted_immediately(self):
        saved_states = []
        reader = DesktopReader(
            ["第一条。", "第二条。"],
            {
                "index": 0,
                "x": 100,
                "y": 80,
                "width": 900,
                "font_size": 14,
                "opacity": 0.85,
                "shortcuts": {
                    "font_wheel": "Ctrl",
                    "opacity_wheel": "Shift",
                },
            },
            file_path="D:/books/test.txt",
            save_callback=saved_states.append,
        )
        self.addCleanup(reader.close)

        reader.apply_settings(
            {
                "font_size": 20,
                "opacity": 0.7,
                "shortcuts": {
                    "font_wheel": "Alt",
                    "opacity_wheel": "Ctrl",
                },
            }
        )

        self.assertEqual(reader.font_size, 20)
        self.assertEqual(reader.visible_opacity, 0.7)
        self.assertEqual(reader.font_wheel_modifier, "Alt")
        self.assertEqual(reader.opacity_wheel_modifier, "Ctrl")
        self.assertEqual(len(saved_states), 1)
        self.assertEqual(saved_states[0]["shortcuts"]["font_wheel"], "Alt")

    def test_open_settings_applies_accepted_dialog_values(self):
        reader = self.make_reader()
        self.addCleanup(reader.close)
        values = {
            "font_size": 18,
            "opacity": 0.65,
            "shortcuts": {
                "font_wheel": "Shift",
                "opacity_wheel": "Alt",
            },
        }

        with patch("reader_window.SettingsDialog") as dialog_type:
            dialog = dialog_type.return_value
            dialog.exec.return_value = QDialog.DialogCode.Accepted
            dialog.get_settings.return_value = values
            reader.open_settings()

        self.assertEqual(reader.font_size, 18)
        self.assertEqual(reader.font_wheel_modifier, "Shift")
        self.assertEqual(reader.opacity_wheel_modifier, "Alt")

    def test_context_menu_contains_open_settings_and_exit(self):
        reader = DesktopReader(
            ["第一条。", "第二条。"],
            self.make_state(),
            open_weread_callback=lambda parent, saved: self._chapter(
                "book-1", "chapter-1", ["正文。"]
            ),
        )
        self.addCleanup(reader.close)

        menu = reader.create_context_menu()
        self.addCleanup(menu.close)
        labels = [action.text() for action in menu.actions() if not action.isSeparator()]

        self.assertEqual(labels, ["打开文件", "阅读详情", "设置", "退出"])

    def test_exported_weread_state_contains_book_chapter_and_line_index(self):
        chapter = self._chapter("book-1", "chapter-6", ["甲。", "乙。", "丙。"])
        reader = DesktopReader(
            chapter.units,
            self.make_state(source="weread"),
            source_type="weread",
            weread_chapter=chapter,
        )
        self.addCleanup(reader.close)
        reader.index = 2

        state = reader.get_state()

        self.assertEqual(state["source"], "weread")
        self.assertEqual(
            state["weread"]["books"]["book-1"],
            {
                "book_id": "book-1",
                "book_title": "测试书",
                "chapter_id": "chapter-6",
                "chapter_title": "第六章",
                "chapter_url": "https://weread.qq.com/web/reader/book-1-chapter-6",
                "line_index": 2,
            },
        )

    def test_weread_menu_contains_book_and_chapter_runtime_actions(self):
        chapter = self._chapter("book-1", "chapter-1", ["第一行。", "第二行。"])
        reader = DesktopReader(
            chapter.units,
            self.make_state(source="weread"),
            source_type="weread",
            weread_chapter=chapter,
            open_weread_callback=lambda parent, saved: chapter,
            switch_book_callback=lambda parent, current: chapter,
            chapter_change_callback=lambda parent, direction: chapter,
        )
        self.addCleanup(reader.close)

        menu = reader.create_context_menu()
        self.addCleanup(menu.close)
        labels = [action.text() for action in menu.actions() if not action.isSeparator()]

        self.assertEqual(
            labels,
            ["打开微信读书", "切换书籍", "上一章", "下一章", "阅读详情", "设置", "退出"],
        )

    def test_weread_details_show_requested_metadata_and_progress(self):
        chapter = self._chapter("book-1", "chapter-6", ["甲。", "乙。", "丙。"])
        reader = DesktopReader(
            chapter.units,
            self.make_state(source="weread"),
            source_type="weread",
            weread_chapter=chapter,
        )
        self.addCleanup(reader.close)
        reader.index = 1

        self.assertEqual(
            reader.reading_detail_lines(),
            [
                "当前来源：微信读书",
                "当前书籍：《测试书》",
                "当前章节：第六章",
                "当前章节进度：2 / 3 行",
            ],
        )

    def test_end_of_weread_chapter_automatically_loads_next_chapter(self):
        first = self._chapter("book-1", "chapter-1", ["末行。"])
        second = self._chapter("book-1", "chapter-2", ["新章第一行。", "新章第二行。"])
        calls = []
        reader = DesktopReader(
            first.units,
            self.make_state(source="weread"),
            source_type="weread",
            weread_chapter=first,
            chapter_change_callback=lambda parent, direction: calls.append(direction) or second,
        )
        self.addCleanup(reader.close)

        reader.navigate(1)

        self.assertEqual(calls, [1])
        self.assertEqual(reader.chapter_id, "chapter-2")
        self.assertEqual(reader.index, 0)
        self.assertEqual(reader.label.text(), "新章第一行。")

    def test_switching_books_restores_saved_chapter_line(self):
        first = self._chapter("book-1", "chapter-1", ["一。", "二。"])
        second = self._chapter("book-2", "chapter-8", ["甲。", "乙。", "丙。"])
        state = self.make_state(
            source="weread",
            weread={
                "active_book_id": "book-1",
                "books": {
                    "book-2": {
                        "book_id": "book-2",
                        "book_title": "测试书",
                        "chapter_id": "chapter-8",
                        "chapter_title": "第六章",
                        "chapter_url": second.chapter_url,
                        "line_index": 2,
                    }
                },
            },
        )
        reader = DesktopReader(
            first.units,
            state,
            source_type="weread",
            weread_chapter=first,
            switch_book_callback=lambda parent, current: second,
        )
        self.addCleanup(reader.close)

        reader.switch_weread_book()

        self.assertEqual(reader.book_id, "book-2")
        self.assertEqual(reader.index, 2)
        self.assertEqual(reader.label.text(), "丙。")

    def test_open_text_file_replaces_units_resets_index_and_saves(self):
        saved_states = []
        callback_calls = []

        def choose_file(parent, current_file):
            callback_calls.append((parent, current_file))
            return "D:/books/new.txt", ["新书第一条。", "新书第二条。"]

        reader = DesktopReader(
            ["旧书第一条。", "旧书第二条。"],
            {
                "index": 1,
                "x": 100,
                "y": 80,
                "width": 900,
                "font_size": 14,
                "opacity": 0.85,
                "shortcuts": {
                    "font_wheel": "Ctrl",
                    "opacity_wheel": "Shift",
                },
            },
            file_path="D:/books/old.txt",
            save_callback=saved_states.append,
            open_file_callback=choose_file,
        )
        self.addCleanup(reader.close)

        reader.open_text_file()

        self.assertEqual(callback_calls, [(reader, "D:/books/old.txt")])
        self.assertEqual(reader.file_path, "D:/books/new.txt")
        self.assertEqual(reader.index, 0)
        self.assertEqual(reader.label.text(), "新书第一条。")
        self.assertEqual(len(saved_states), 1)
        self.assertEqual(saved_states[0]["file"], "D:/books/new.txt")
        self.assertEqual(saved_states[0]["index"], 0)

    def test_cancel_open_text_file_keeps_current_content(self):
        saved_states = []
        reader = DesktopReader(
            ["旧书第一条。", "旧书第二条。"],
            {
                "index": 1,
                "x": 100,
                "y": 80,
                "width": 900,
                "font_size": 14,
                "opacity": 0.85,
                "shortcuts": {
                    "font_wheel": "Ctrl",
                    "opacity_wheel": "Shift",
                },
            },
            file_path="D:/books/old.txt",
            save_callback=saved_states.append,
            open_file_callback=lambda parent, current: None,
        )
        self.addCleanup(reader.close)

        reader.open_text_file()

        self.assertEqual(reader.file_path, "D:/books/old.txt")
        self.assertEqual(reader.index, 1)
        self.assertEqual(reader.label.text(), "旧书第二条。")
        self.assertEqual(saved_states, [])

    @staticmethod
    def _wheel_event(x=0, y=0, modifiers=Qt.KeyboardModifier.NoModifier):
        return QWheelEvent(
            QPointF(10, 10),
            QPointF(10, 10),
            QPoint(),
            QPoint(x, y),
            Qt.MouseButton.NoButton,
            modifiers,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )

    @staticmethod
    def make_state(**overrides):
        state = {
            "index": 0,
            "x": 100,
            "y": 80,
            "width": 900,
            "font_size": 14,
            "opacity": 0.85,
            "shortcuts": {"font_wheel": "Ctrl", "opacity_wheel": "Shift"},
        }
        state.update(overrides)
        return state

    @staticmethod
    def _chapter(book_id, chapter_id, units):
        chapter_number = "第六章" if chapter_id == "chapter-6" else chapter_id
        return WeReadChapter(
            book_id=book_id,
            book_title="测试书",
            chapter_id=chapter_id,
            chapter_title=chapter_number,
            chapter_url=f"https://weread.qq.com/web/reader/{book_id}-{chapter_id}",
            units=tuple(units),
        )


if __name__ == "__main__":
    unittest.main()
