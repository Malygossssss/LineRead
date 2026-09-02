# Lazy Canvas Chapter Capture Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Capture every lazily rendered Canvas in a long WeRead chapter instead of stopping at the Canvas set present at the first DOM query.

**Architecture:** Scan the current Canvas set repeatedly while advancing the chapter's actual scroll container in one direction. Export Canvas PNG data directly from the DOM so capture never scrolls an element back into view. Deduplicate both pixel hashes and recognized text chunks, then stop only after the scroll container is at the bottom with no unseen text for consecutive scans.

**Tech Stack:** Python 3.10+, Playwright synchronous API, Pillow, RapidOCR, hashlib, unittest mocks.

---

### Task 1: Reproduce dynamic Canvas discovery

**Files:**
- Modify: `tests/test_weread_source.py`

**Step 1: Write a failing test**

Create a locator whose Canvas count grows across scans and assert that OCR returns text from every newly discovered Canvas in reading order.

**Step 2: Run the focused test**

Run: `.venv\Scripts\python.exe -m unittest tests.test_weread_source.WeReadControllerChapterTests.test_ocr_discovers_lazily_appended_canvases -v`

Expected: FAIL because `_ocr_current_canvas()` snapshots `count()` once.

### Task 2: Implement scroll-and-discover capture

**Files:**
- Modify: `weread_source.py`
- Test: `tests/test_weread_source.py`

**Step 1: Add discovery limits**

Define a short render wait, three stable bottom scans, and a bounded maximum scan count.

**Step 2: Add scroll-container advancement**

Evaluate DOM JavaScript that finds the nearest vertical scroll container for `.readerChapterContent`, advances it toward its current bottom, and reports whether it is at the bottom.

**Step 3: Capture Canvas without element scrolling**

Export PNG data from each Canvas with `toDataURL`, falling back to an element screenshot only when direct export is unavailable. Hash each PNG locally with SHA-256 and deduplicate recognized text chunks, preserving discovery order and filtering the chapter header only from the first unique Canvas.

**Step 4: Require stable completion**

Stop only after three bottom scans produce no unseen text. Raise a user-facing error if the maximum scan count is reached.

**Step 5: Run focused tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_weread_source.WeReadControllerChapterTests -v`

Expected: PASS.

### Task 3: Regression verification and documentation

**Files:**
- Modify: `README.md`

**Step 1: Document lazy Canvas capture**

Explain that long chapters are advanced until rendering stabilizes before OCR is considered complete.

**Step 2: Run the full suite**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: all tests PASS.

**Step 3: Run static checks**

Run: `.venv\Scripts\python.exe -m py_compile weread_source.py tests\test_weread_source.py`

Run: `git diff --check`

Expected: both commands exit successfully.
