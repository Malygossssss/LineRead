"""Application entry point for the desktop single-line reader."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from config import CONFIG_PATH, load_config, save_config
from reader_window import DesktopReader
from text_parser import TxtSource


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("单行阅读")
    app.setQuitOnLastWindowClosed(True)

    state = load_config(CONFIG_PATH)
    requested_path = sys.argv[1] if len(sys.argv) > 1 else state.get("file", "")
    path = Path(requested_path).expanduser() if requested_path else None

    while True:
        if path is None or not path.is_file():
            if path is not None:
                QMessageBox.warning(None, "文件不存在", f"找不到 TXT 文件：\n{path}")
            selected, _ = QFileDialog.getOpenFileName(
                None,
                "选择 UTF-8 TXT 文件",
                str(path.parent) if path is not None else "",
                "Text files (*.txt)",
            )
            if not selected:
                return 0
            path = Path(selected)

        try:
            units = TxtSource(path).get_units()
            break
        except (OSError, ValueError) as exc:
            QMessageBox.warning(None, "无法打开 TXT", str(exc))
            path = None

    absolute_path = str(path.resolve())
    if not _same_file(state.get("file", ""), absolute_path):
        state["index"] = 0

    reader = DesktopReader(
        units,
        state,
        file_path=absolute_path,
        save_callback=lambda current: save_config(current, CONFIG_PATH),
    )
    reader.show()
    return app.exec()


def _same_file(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


if __name__ == "__main__":
    raise SystemExit(main())
