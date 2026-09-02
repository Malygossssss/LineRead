"""Searchable chapter selection for the current WeRead catalog."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from weread_source import WeReadCatalogEntry


CHAPTER_ID_ROLE = Qt.ItemDataRole.UserRole


class ChapterSelectionDialog(QDialog):
    """Filter a cached catalog and return one stable chapter id."""

    def __init__(
        self,
        entries: Sequence[WeReadCatalogEntry],
        current_chapter_id: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择章节")
        self.setModal(True)
        self.resize(520, 560)

        prompt = QLabel("搜索并选择要阅读的章节：", self)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("输入章节名…")
        self.search.setClearButtonEnabled(True)
        self.chapter_list = QListWidget(self)
        self.chapter_list.setAlternatingRowColors(True)

        current_item: QListWidgetItem | None = None
        for entry in entries:
            indent = "    " * max(0, entry.level - 1)
            item = QListWidgetItem(f"{indent}{entry.title}")
            item.setData(CHAPTER_ID_ROLE, entry.chapter_id)
            item.setToolTip(entry.title)
            self.chapter_list.addItem(item)
            if entry.chapter_id == current_chapter_id:
                current_item = item

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.search.textChanged.connect(self._filter_entries)
        self.chapter_list.itemDoubleClicked.connect(lambda _item: self.accept())

        layout = QVBoxLayout(self)
        layout.addWidget(prompt)
        layout.addWidget(self.search)
        layout.addWidget(self.chapter_list, 1)
        layout.addWidget(buttons)

        if current_item is not None:
            self.chapter_list.setCurrentItem(current_item)
            self.chapter_list.scrollToItem(current_item)
        elif self.chapter_list.count():
            self.chapter_list.setCurrentRow(0)

    def selected_chapter_id(self) -> str:
        item = self.chapter_list.currentItem()
        value = item.data(CHAPTER_ID_ROLE) if item is not None else ""
        return value if isinstance(value, str) else ""

    def _filter_entries(self, query: str) -> None:
        folded_query = query.strip().casefold()
        first_visible: QListWidgetItem | None = None
        current = self.chapter_list.currentItem()
        for index in range(self.chapter_list.count()):
            item = self.chapter_list.item(index)
            visible = not folded_query or folded_query in item.toolTip().casefold()
            item.setHidden(not visible)
            if visible and first_visible is None:
                first_visible = item
        if current is None or current.isHidden():
            self.chapter_list.setCurrentItem(first_visible)
