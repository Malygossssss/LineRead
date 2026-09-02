import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from chapter_selection_dialog import ChapterSelectionDialog
from weread_source import WeReadCatalogEntry


class ChapterSelectionDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_dialog(self):
        dialog = ChapterSelectionDialog(
            (
                WeReadCatalogEntry("chapter-1", "序言", 1),
                WeReadCatalogEntry("chapter-2", "黄金时代", 1),
                WeReadCatalogEntry("chapter-3", "第六章", 2),
            ),
            "chapter-3",
        )
        self.addCleanup(dialog.close)
        return dialog

    def test_selects_and_scrolls_to_current_chapter(self):
        dialog = self.make_dialog()

        self.assertEqual(dialog.selected_chapter_id(), "chapter-3")
        self.assertEqual(dialog.chapter_list.currentItem().text().strip(), "第六章")

    def test_search_filters_titles_and_keeps_stable_id(self):
        dialog = self.make_dialog()

        dialog.search.setText("黄金")

        visible = [
            dialog.chapter_list.item(index)
            for index in range(dialog.chapter_list.count())
            if not dialog.chapter_list.item(index).isHidden()
        ]
        self.assertEqual([item.text().strip() for item in visible], ["黄金时代"])
        self.assertEqual(dialog.selected_chapter_id(), "chapter-2")


if __name__ == "__main__":
    unittest.main()
