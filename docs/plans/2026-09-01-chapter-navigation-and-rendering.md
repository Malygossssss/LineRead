# Chapter Navigation and Rendering Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add searchable direct chapter selection, capture complete long chapters, preserve source lines, and show responsive rendering status during chapter changes.

**Architecture:** Carry the catalog with each immutable chapter snapshot, filter it in a dedicated Qt dialog, and submit chapter work to the existing single Playwright executor. Preserve the previous cached chapter while the UI shows a loading label. Make Canvas completion depend on stable rendered/scroll extent as well as text discovery.

**Tech Stack:** Python 3.10+, PySide6, Playwright synchronous API, Pillow, RapidOCR, unittest.

---

### Task 1: Preserve source line boundaries

**Files:**
- Modify: `text_parser.py`
- Modify: `weread_source.py`
- Modify: `tests/test_text_parser.py`
- Modify: `tests/test_weread_source.py`

**Steps:**
1. Replace punctuation/max-width assertions with non-empty physical-line assertions.
2. Run the focused parser/source tests and observe the old splitting behavior fail.
3. Preserve cleaned physical lines and pass OCR/DOM lines through without extra splitting.
4. Run the focused tests and confirm they pass.

### Task 2: Expose and select the complete catalog

**Files:**
- Create: `chapter_selection_dialog.py`
- Modify: `weread_source.py`
- Modify: `reader_window.py`
- Modify: `main.py`
- Modify: `tests/test_weread_source.py`
- Modify: `tests/test_reader_window.py`

**Steps:**
1. Add tests for catalog propagation, stable-id selection, filtering, and current item selection.
2. Add immutable catalog entries to `WeReadChapter` and expose controller/source direct selection.
3. Add the searchable dialog and the “选择章节…” context-menu action.
4. Run the source and reader tests.

### Task 3: Make chapter rendering asynchronous

**Files:**
- Modify: `main.py`
- Modify: `reader_window.py`
- Modify: `tests/test_main.py`
- Modify: `tests/test_reader_window.py`

**Steps:**
1. Add tests that chapter requests return immediately, emit completion/error signals, and keep navigation blocked while loading.
2. Submit adjacent/direct chapter operations to the existing one-worker executor.
3. Display “文本渲染中…” immediately while retaining the old cached chapter for recovery.
4. Restore the old line on failure and atomically apply the new chapter on success.
5. Run integration and window tests.

### Task 4: Prevent premature long-chapter completion

**Files:**
- Modify: `weread_source.py`
- Modify: `tests/test_weread_source.py`

**Steps:**
1. Add a failing test where the page reports a temporary bottom before delayed Canvas/text growth.
2. Track Canvas count, image/text discovery, scroll position, and maximum extent across passes.
3. Reset completion stability whenever any observed extent changes and require a sustained stable end.
4. Run all controller/source tests.

### Task 5: Documentation and regression verification

**Files:**
- Modify: `README.md`

**Steps:**
1. Document direct chapter selection, line preservation, complete lazy capture, and rendering status.
2. Run `python -m unittest discover -s tests -v` with the project interpreter.
3. Run `python -m py_compile` for application and test modules.
4. Run `git diff --check` and review the final diff.
