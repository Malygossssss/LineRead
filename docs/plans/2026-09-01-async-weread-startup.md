# Async WeRead Startup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Start LineRead without a login modal, show live login/book/rendering states in the floating reader, and replace the state text with chapter content when WeRead is ready.

**Architecture:** Create the reader window before connecting to WeRead. Run all Playwright work on one dedicated executor thread and send state/result/error changes back to Qt through signals, preserving Playwright thread affinity for later chapter and book operations.

**Tech Stack:** Python 3.10+, PySide6 signals/widgets, `concurrent.futures.ThreadPoolExecutor`, Playwright synchronous API, unittest/QSignalSpy.

---

### Task 1: Reader readiness detection

**Files:**
- Modify: `weread_source.py`
- Test: `tests/test_weread_source.py`

**Step 1: Write failing tests**

Add controller tests proving a reader URL reports `reader`, a visible login state reports `login`, and a logged-in non-reader page reports `book`.

**Step 2: Verify failure**

Run: `.venv\Scripts\python.exe -m unittest tests.test_weread_source.WeReadControllerChapterTests -v`

Expected: FAIL because the readiness API does not exist.

**Step 3: Implement minimal readiness API**

Add a DOM-only `readiness_state()` method and JavaScript extractor. Do not read storage, browser caches, or private APIs.

**Step 4: Verify pass**

Run the same controller test command and expect PASS.

### Task 2: Asynchronous startup coordinator

**Files:**
- Modify: `main.py`
- Test: `tests/test_main.py`

**Step 1: Write failing tests**

Cover status order `正在连接微信读书… -> 等待登录… -> 等待选书… -> 文本渲染中…`, successful chapter delivery, error delivery without a message box, and startup wiring before `app.exec()`.

**Step 2: Verify failure**

Run: `.venv\Scripts\python.exe -m unittest tests.test_main -v`

Expected: FAIL because startup is synchronous and modal.

**Step 3: Implement executor and Qt signals**

Make `WeReadIntegration` a `QObject` with status, ready, and failed signals. Poll readiness on a single worker, emit rendering state before OCR, and route later browser calls through the same worker.

**Step 4: Verify pass**

Run the same main test command and expect PASS.

### Task 3: Floating reader loading states

**Files:**
- Modify: `reader_window.py`
- Test: `tests/test_reader_window.py`

**Step 1: Write failing tests**

Prove loading text appears in the label, navigation is disabled while loading, errors remain visible, and a ready chapter restores its saved line.

**Step 2: Verify failure**

Run: `.venv\Scripts\python.exe -m unittest tests.test_reader_window -v`

Expected: FAIL because loading-state methods do not exist.

**Step 3: Implement loading-state methods**

Add public slots for status, failure, and ready chapter application. Keep state text transient and do not persist it as reading progress.

**Step 4: Verify pass**

Run the same reader test command and expect PASS.

### Task 4: Documentation and full verification

**Files:**
- Modify: `README.md`

**Step 1: Update behavior documentation**

Document automatic login/book detection and the three floating-window statuses.

**Step 2: Run full verification**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: all tests PASS.

Run: `.venv\Scripts\python.exe -m py_compile main.py reader_window.py weread_source.py`

Expected: exit code 0.

Run: `git diff --check`

Expected: no whitespace errors.
