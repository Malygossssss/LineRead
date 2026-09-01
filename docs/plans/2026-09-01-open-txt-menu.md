# Open TXT From Context Menu Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let users choose a new local TXT from the reader's right-click menu, start it at the first unit, and save the new path immediately.

**Architecture:** Keep `TxtSource` and file dialogs in `main.py`, exposed through an injected callback. `DesktopReader` remains source-agnostic and only replaces a supplied unit list and source identifier. Cancelled or failed loads return no result and leave active content unchanged.

**Tech Stack:** Python 3.10+, PySide6, existing `TxtSource`, JSON configuration, unittest.

---

### Task 1: Reader replacement behavior

**Files:**
- Modify: `tests/test_reader_window.py`
- Modify: `reader_window.py`

**Steps:**
1. Add failing tests for context-menu labels, successful replacement, index reset, immediate save, and cancellation.
2. Add the injected callback, `open_text_file`, and source-agnostic `replace_content` methods.
3. Wire “打开 TXT…” before Settings in the existing context menu.
4. Run reader tests.

### Task 2: TXT selection callback

**Files:**
- Create: `tests/test_main.py`
- Modify: `main.py`

**Steps:**
1. Add tests for successful selection, cancel, and invalid-file retry/error handling.
2. Implement a reusable function that opens `QFileDialog`, loads through `TxtSource`, and returns `(absolute_path, units)`.
3. Inject the function into `DesktopReader` during application startup.
4. Run main integration tests.

### Task 3: Documentation and verification

**Files:**
- Modify: `README.md`

**Steps:**
1. Document the new right-click menu item and first-unit reset behavior.
2. Run all unit tests and Python compilation.
3. Run an end-to-end replacement and persisted-state reload check.
