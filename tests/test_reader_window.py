import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QDialog

from reader_window import DesktopReader


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


if __name__ == "__main__":
    unittest.main()
