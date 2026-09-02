import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QDialog

from reader_window import DesktopReader
from weread_source import WeReadCatalogEntry, WeReadChapter


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
        return DesktopReader(
            ["第一条。", "第二条。", "第三条。"],
            state,
            source_type="txt",
        )

    def test_uses_required_floating_window_flags(self):
        reader = self.make_reader()
        self.addCleanup(reader.close)

        flags = reader.windowFlags()
        self.assertTrue(flags & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(flags & Qt.WindowType.WindowStaysOnTopHint)
        self.assertTrue(flags & Qt.WindowType.Tool)
        self.assertTrue(reader.testAttribute(Qt.WidgetAttribute.WA_QuitOnClose))

    def test_restored_position_is_clamped_to_the_available_desktop(self):
        available = QRect(0, 0, 1440, 852)

        with patch(
            "reader_window._available_screen_geometries",
            return_value=(available,),
        ):
            reader = self.make_reader(x=-94, y=856)
        self.addCleanup(reader.close)

        self.assertEqual(reader.pos().x(), 0)
        self.assertEqual(reader.pos().y(), available.bottom() - reader.height() + 1)

    def test_restore_and_activate_recovers_a_window_moved_offscreen(self):
        available = QRect(0, 0, 1440, 852)
        reader = self.make_reader()
        self.addCleanup(reader.close)
        reader.move(-1200, 1000)

        with patch(
            "reader_window._available_screen_geometries",
            return_value=(available,),
        ):
            reader.restore_and_activate()

        self.assertEqual(
            reader.pos(),
            QPoint(0, available.bottom() - reader.height() + 1),
        )
        self.assertAlmostEqual(
            reader.windowOpacity(),
            reader.visible_opacity,
            delta=1 / 255,
        )

    def test_navigation_stays_within_bounds(self):
        reader = self.make_reader(index=0)
        self.addCleanup(reader.close)

        reader.navigate(-1)
        self.assertEqual(reader.index, 0)
        reader.navigate(1)
        self.assertEqual(reader.label.text(), "第二条。")
        reader.navigate(99)
        self.assertEqual(reader.index, 2)

    def test_loading_status_replaces_text_and_blocks_navigation(self):
        reader = self.make_reader(index=1)
        self.addCleanup(reader.close)

        reader.set_loading_status("等待登录…")
        reader.navigate(1)

        self.assertEqual(reader.label.text(), "等待登录…")
        self.assertEqual(reader.index, 0)

    def test_loading_error_is_shown_in_the_reader(self):
        reader = self.make_reader()
        self.addCleanup(reader.close)

        reader.show_loading_error("正文读取失败")

        self.assertEqual(reader.label.text(), "连接失败：正文读取失败")

    def test_ready_chapter_ignores_legacy_saved_line_and_starts_at_first(self):
        chapter = self._chapter("book-1", "chapter-6", ["甲。", "乙。", "丙。"])
        state = self.make_state(
            source="weread",
            weread={
                "active_book_id": "book-1",
                "books": {
                    "book-1": {
                        "book_id": "book-1",
                        "book_title": "测试书",
                        "chapter_id": "chapter-6",
                        "chapter_title": "第六章",
                        "chapter_url": chapter.chapter_url,
                        "line_index": 1,
                    }
                },
            },
        )
        reader = DesktopReader(
            ["正在连接微信读书…"],
            state,
            source_type="weread",
        )
        self.addCleanup(reader.close)
        reader.set_loading_status("文本渲染中…")

        reader.load_weread_chapter(chapter)

        self.assertEqual(reader.index, 0)
        self.assertEqual(reader.label.text(), "甲。")

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
            source_type="txt",
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
            source_type="txt",
            open_weread_callback=lambda parent: self._chapter(
                "book-1", "chapter-1", ["正文。"]
            ),
        )
        self.addCleanup(reader.close)

        menu = reader.create_context_menu()
        self.addCleanup(menu.close)
        labels = [action.text() for action in menu.actions() if not action.isSeparator()]

        self.assertEqual(labels, ["打开文件", "阅读详情", "设置", "退出"])

    def test_exported_state_does_not_contain_weread_progress(self):
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
        self.assertEqual(state["index"], 0)
        self.assertNotIn("weread", state)

    def test_weread_menu_contains_book_and_chapter_runtime_actions(self):
        chapter = self._chapter("book-1", "chapter-1", ["第一行。", "第二行。"])
        reader = DesktopReader(
            chapter.units,
            self.make_state(source="weread"),
            source_type="weread",
            weread_chapter=chapter,
            open_weread_callback=lambda parent: chapter,
            switch_book_callback=lambda parent: chapter,
            chapter_change_callback=lambda parent, direction: True,
            chapter_select_callback=lambda parent, chapter_id: True,
        )
        self.addCleanup(reader.close)

        menu = reader.create_context_menu()
        self.addCleanup(menu.close)
        labels = [action.text() for action in menu.actions() if not action.isSeparator()]

        self.assertEqual(
            labels,
            [
                "打开微信读书",
                "切换书籍",
                "选择章节…",
                "上一章",
                "下一章",
                "阅读详情",
                "设置",
                "退出",
            ],
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
                "当前章节：第六章",
                "当前页面进度：2 / 3 行",
            ],
        )

    def test_end_of_weread_page_lazily_loads_next_page(self):
        first = self._chapter("book-1", "chapter-1", ["末行。"])
        calls = []
        reader = DesktopReader(
            first.units,
            self.make_state(source="weread"),
            source_type="weread",
            weread_chapter=first,
            page_change_callback=lambda parent, direction: calls.append(direction) or True,
        )
        self.addCleanup(reader.close)

        reader.navigate(1)

        self.assertEqual(calls, [1])
        self.assertTrue(reader._loading)
        self.assertEqual(reader.index, 0)
        self.assertEqual(reader.label.text(), "文本渲染中…")

    def test_start_of_weread_page_lazily_loads_previous_page(self):
        current = self._chapter("book-1", "chapter-1", ["首行。", "末行。"])
        calls = []
        reader = DesktopReader(
            current.units,
            self.make_state(source="weread"),
            source_type="weread",
            weread_chapter=current,
            page_change_callback=lambda parent, direction: calls.append(direction) or True,
        )
        self.addCleanup(reader.close)

        reader.navigate(-1)

        self.assertEqual(calls, [-1])
        self.assertTrue(reader._loading)

    def test_finished_page_turn_uses_first_or_last_line_by_direction(self):
        current = self._chapter("book-1", "chapter-1", ["当前页。"])
        target = self._chapter("book-1", "chapter-1", ["甲。", "乙。", "丙。"])
        reader = DesktopReader(
            current.units,
            self.make_state(source="weread"),
            source_type="weread",
            weread_chapter=current,
            page_change_callback=lambda parent, direction: True,
        )
        self.addCleanup(reader.close)

        reader.navigate(1)
        reader.finish_page_change(target, 1)
        self.assertEqual(reader.index, 0)
        self.assertEqual(reader.label.text(), "甲。")

        reader.navigate(-1)
        reader.finish_page_change(target, -1)
        self.assertEqual(reader.index, 2)
        self.assertEqual(reader.label.text(), "丙。")

    def test_failed_page_turn_restores_previous_page_line(self):
        current = self._chapter("book-1", "chapter-1", ["第一行。", "第二行。"])
        reader = DesktopReader(
            current.units,
            self.make_state(source="weread"),
            source_type="weread",
            weread_chapter=current,
            page_change_callback=lambda parent, direction: True,
        )
        self.addCleanup(reader.close)
        reader.index = 1
        reader.navigate(1)

        with patch("reader_window.QMessageBox.warning") as warning:
            reader.fail_page_change("页面读取失败")

        self.assertFalse(reader._loading)
        self.assertEqual(reader.index, 1)
        self.assertEqual(reader.label.text(), "第二行。")
        warning.assert_called_once()

    def test_finished_chapter_change_replaces_loading_status(self):
        first = self._chapter("book-1", "chapter-1", ["末行。"])
        second = self._chapter("book-1", "chapter-2", ["新章第一行。", "新章第二行。"])
        reader = DesktopReader(
            first.units,
            self.make_state(source="weread"),
            source_type="weread",
            weread_chapter=first,
            chapter_change_callback=lambda parent, direction: True,
        )
        self.addCleanup(reader.close)

        reader.change_chapter(1)
        reader.finish_chapter_change(second)

        self.assertFalse(reader._loading)
        self.assertEqual(reader.chapter_id, "chapter-2")
        self.assertEqual(reader.label.text(), "新章第一行。")

    def test_failed_chapter_change_restores_previous_line(self):
        chapter = self._chapter("book-1", "chapter-1", ["第一行。", "第二行。"])
        reader = DesktopReader(
            chapter.units,
            self.make_state(source="weread", index=1),
            source_type="weread",
            weread_chapter=chapter,
            chapter_change_callback=lambda parent, direction: True,
        )
        self.addCleanup(reader.close)
        reader.index = 1

        reader.change_chapter(1)
        with patch("reader_window.QMessageBox.warning") as warning:
            reader.fail_chapter_change("正文读取失败")

        self.assertFalse(reader._loading)
        self.assertEqual(reader.index, 1)
        self.assertEqual(reader.label.text(), "第二行。")
        warning.assert_called_once()

    def test_chapter_picker_submits_selected_stable_id(self):
        chapter = self._chapter("book-1", "chapter-1", ["正文。"])
        selected = []
        reader = DesktopReader(
            chapter.units,
            self.make_state(source="weread"),
            source_type="weread",
            weread_chapter=chapter,
            chapter_select_callback=lambda parent, chapter_id: selected.append(chapter_id) or True,
        )
        self.addCleanup(reader.close)

        with patch("reader_window.ChapterSelectionDialog") as dialog_type:
            dialog = dialog_type.return_value
            dialog.exec.return_value = QDialog.DialogCode.Accepted
            dialog.selected_chapter_id.return_value = "chapter-2"
            reader.choose_chapter()

        self.assertEqual(selected, ["chapter-2"])
        self.assertEqual(reader.label.text(), "文本渲染中…")

    def test_switching_books_starts_at_the_first_line(self):
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
            switch_book_callback=lambda parent: second,
        )
        self.addCleanup(reader.close)

        reader.switch_weread_book()

        self.assertEqual(reader.book_id, "book-2")
        self.assertEqual(reader.index, 0)
        self.assertEqual(reader.label.text(), "甲。")

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
            source_type="txt",
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
            source_type="txt",
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
            catalog=(
                WeReadCatalogEntry("chapter-1", "第一章", 1),
                WeReadCatalogEntry("chapter-2", "第二章", 1),
                WeReadCatalogEntry("chapter-6", "第六章", 1),
                WeReadCatalogEntry("chapter-8", "第八章", 1),
            ),
            catalog_index=0,
        )


if __name__ == "__main__":
    unittest.main()
