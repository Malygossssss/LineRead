import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog

from settings_dialog import SettingsDialog


class SettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_dialog(self):
        dialog = SettingsDialog(
            {
                "font_size": 16,
                "opacity": 0.75,
                "shortcuts": {
                    "font_wheel": "Alt",
                    "opacity_wheel": "Ctrl",
                },
            }
        )
        self.addCleanup(dialog.close)
        return dialog

    def test_initializes_controls_from_reader_state(self):
        dialog = self.make_dialog()

        self.assertEqual(dialog.font_size_spin.value(), 16)
        self.assertEqual(dialog.opacity_spin.value(), 0.75)
        self.assertEqual(dialog.font_modifier_combo.currentData(), "Alt")
        self.assertEqual(dialog.opacity_modifier_combo.currentData(), "Ctrl")

    def test_exports_changed_settings(self):
        dialog = self.make_dialog()
        dialog.font_size_spin.setValue(22)
        dialog.opacity_spin.setValue(0.9)
        dialog.font_modifier_combo.setCurrentIndex(
            dialog.font_modifier_combo.findData("Shift")
        )
        dialog.opacity_modifier_combo.setCurrentIndex(
            dialog.opacity_modifier_combo.findData("Alt")
        )

        self.assertEqual(
            dialog.get_settings(),
            {
                "font_size": 22,
                "opacity": 0.9,
                "shortcuts": {
                    "font_wheel": "Shift",
                    "opacity_wheel": "Alt",
                },
            },
        )

    def test_rejects_duplicate_wheel_modifiers_inline(self):
        dialog = self.make_dialog()
        ctrl_index = dialog.font_modifier_combo.findData("Ctrl")
        dialog.font_modifier_combo.setCurrentIndex(ctrl_index)
        dialog.opacity_modifier_combo.setCurrentIndex(ctrl_index)

        dialog.accept_if_valid()

        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertTrue(dialog.error_label.isVisibleTo(dialog))
        self.assertIn("不能相同", dialog.error_label.text())

    def test_accepts_distinct_wheel_modifiers(self):
        dialog = self.make_dialog()

        dialog.accept_if_valid()

        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)


if __name__ == "__main__":
    unittest.main()
