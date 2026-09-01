# WeRead Support Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add browser-driven WeRead reading while preserving the existing TXT parser, single-line floating window, and settings behavior.

**Architecture:** A synchronous `WeReadController` owns one Playwright persistent Chromium context and interacts only with the visible WeRead web page. `WeReadSource` converts the active chapter DOM into the existing punctuation-aware reading units and caches the resulting chapter snapshot in memory. `DesktopReader` remains the presentation layer; injected callbacks switch sources or chapters, while the normalized config stores one `book_id/chapter_id/line_index` position per WeRead book.

**Tech Stack:** Python 3.10+, PySide6, Playwright Chromium, standard-library JSON/dataclasses, and unittest with mocked browser pages.

---

### Task 1: Configuration schema and reading metadata

**Files:**
- Modify: `config.py`
- Modify: `tests/test_config.py`

**Steps:**
1. Add failing tests for valid and malformed `source` and `weread` position data.
2. Extend config normalization without breaking the existing top-level TXT `file/index` keys.
3. Store the active WeRead book and a normalized position containing `book_id`, `book_title`, `chapter_id`, `chapter_title`, `chapter_url`, and `line_index`.
4. Run the config tests.

### Task 2: Playwright controller and WeRead source

**Files:**
- Create: `weread_source.py`
- Create: `tests/test_weread_source.py`

**Steps:**
1. Add tests around DOM result validation, punctuation-aware unit creation, memory caching, profile-path selection, and chapter navigation delegation.
2. Implement lazy Playwright imports so TXT reading still reports a clear actionable error if the optional runtime is missing.
3. Launch a headed persistent Chromium profile, reuse an existing WeRead page, and expose connect, current chapter, previous/next chapter, restore window, and close operations.
4. Extract only the rendered current chapter from the page DOM; do not read browser caches or private local data.
5. Add resilient selector fallbacks and user-facing errors for login, non-reader pages, empty chapters, and changed WeRead markup.
6. Run the source tests without requiring network access.

### Task 3: Source-aware reader and read-only details

**Files:**
- Create: `reading_details_dialog.py`
- Modify: `reader_window.py`
- Modify: `tests/test_reader_window.py`

**Steps:**
1. Add failing offscreen Qt tests for TXT/WeRead menu contents, details text, manual chapter changes, automatic next chapter at the final line, and per-book progress restoration.
2. Add source metadata and injected WeRead callbacks while retaining the old constructor defaults.
3. Persist the outgoing book before source/book/chapter changes and restore a stored line only when both book and chapter match.
4. Make forward navigation at the last WeRead line fetch and cache the next chapter; retain clamped navigation for TXT.
5. Build the requested menu groups and a small read-only details dialog. Keep runtime book/chapter operations out of Settings.
6. Run the reader tests.

### Task 4: Application wiring and first-login flow

**Files:**
- Modify: `main.py`
- Modify: `tests/test_main.py`

**Steps:**
1. Add tests for callback success/error handling with a mocked WeRead integration.
2. Create the controller lazily when WeRead is first requested or when a saved WeRead source is restored.
3. On first connection, bring Chromium forward and ask the user to scan/login and enter a book before LineRead captures it.
4. For book switching, persist the current position, bring Chromium forward, let the user choose a new book, then recapture and restore that book's LineRead position.
5. Close Playwright cleanly when LineRead exits.
6. Run main integration tests.

### Task 5: Dependency, documentation, and verification

**Files:**
- Modify: `requirements.txt`
- Modify: `README.md`
- Modify: `.gitignore`

**Steps:**
1. Add `playwright` to project requirements.
2. Install it with `.venv\\Scripts\\python.exe -m pip install playwright` and install only Chromium with `.venv\\Scripts\\python.exe -m playwright install chromium`.
3. Document first login, background-browser behavior, source switching, menus, persisted login profile, and known dependency on the WeRead web DOM.
4. Run `python -m unittest discover -s tests -v` in the project virtual environment.
5. Run `python -m py_compile` for all application modules.
6. Run an offline mocked end-to-end smoke test; leave real WeRead login and selector confirmation as an explicit manual check because it requires the user's account and scan.

### Windows browser launch compatibility

On Windows, try Playwright's bundled Chromium first. If Windows returns the
specific process error `spawn UNKNOWN`, or its downloaded executable is
temporarily missing, retry the same persistent profile with Playwright's
installed `chrome` channel. Do not retry
unrelated failures such as a locked user-data directory, and report both launch
errors when neither browser can start.
