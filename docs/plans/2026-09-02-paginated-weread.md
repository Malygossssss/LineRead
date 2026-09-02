# Paginated WeRead Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace full-chapter vertical scanning with exact current-page capture and bidirectional lazy page turns while retaining the reader's existing controls.

**Architecture:** Keep all Playwright access on the existing single worker thread. `WeReadController` owns horizontal layout and public rendered-page capture, `WeReadSource` returns current-page units, `WeReadIntegration` exposes asynchronous page-turn signals, and `DesktopReader` requests a new page only when line navigation crosses a page boundary.

**Tech Stack:** Python 3.13, PySide6, Playwright, Pillow, RapidOCR, `unittest`.

---

### Task 1: Capture only the current horizontal page

**Files:**
- Modify: `weread_source.py`
- Test: `tests/test_weread_source.py`

**Step 1: Write failing tests**

Add controller tests proving that current content switches to horizontal mode, reads only Canvas nodes under the current render target, does not invoke vertical scrolling, and preserves visual line order.

**Step 2: Run the focused tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_weread_source.WeReadControllerChapterTests -v`

Expected: FAIL because horizontal current-page capture does not exist.

**Step 3: Implement the minimal capture path**

Replace `_ensure_vertical_layout` with `_ensure_horizontal_layout`, add current-page Canvas/positioned-text capture, and restrict the DOM payload to content intersecting the current viewport.

**Step 4: Re-run focused tests**

Expected: all controller chapter tests pass.

### Task 2: Add asynchronous bidirectional page turns

**Files:**
- Modify: `weread_source.py`
- Modify: `main.py`
- Test: `tests/test_weread_source.py`
- Test: `tests/test_main.py`

**Step 1: Write failing tests**

Cover public previous/next controls, render-signature waiting, source refresh, worker-thread affinity, ready signals, and errors.

**Step 2: Run the focused tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_weread_source.WeReadSourceTests tests.test_main.WeReadIntegrationTests -v`

Expected: FAIL because page-turn APIs and signals are absent.

**Step 3: Implement controller, source, and integration APIs**

Add `next_page`/`previous_page`, wait until the current public render signature changes, then extract the new page on the existing executor.

**Step 4: Re-run focused tests**

Expected: all source and integration tests pass.

### Task 3: Make line navigation cross page boundaries

**Files:**
- Modify: `reader_window.py`
- Test: `tests/test_reader_window.py`

**Step 1: Write failing tests**

Verify forward boundary starts the new page at its first line, backward boundary starts at its last line, failures restore the old line, and ordinary page-internal scrolling remains local.

**Step 2: Run the focused tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_reader_window.DesktopReaderTests -v`

Expected: FAIL because page-boundary callbacks are absent.

**Step 3: Implement the UI state transition**

Add the page callback and completion/error slots. Change WeRead details to exactly `当前章节：…` and `当前页面进度：x / y 行`.

**Step 4: Re-run focused tests**

Expected: all desktop reader tests pass.

### Task 4: Documentation and regression verification

**Files:**
- Modify: `README.md`
- Test: `tests/`

**Step 1: Update behavior documentation**

Document horizontal current-page capture, lazy turns, page-level details, and the absence of locally persisted WeRead progress.

**Step 2: Compile and run the full suite**

Run: `.venv\Scripts\python.exe -m py_compile config.py main.py reader_window.py weread_source.py`

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: compilation succeeds and all non-environment-blocked tests pass; any pre-existing Windows local-server permission failure is reported separately.
