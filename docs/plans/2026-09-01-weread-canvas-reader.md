# WeRead Canvas Reader Compatibility Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Read and cache the current WeRead chapter when the official web reader renders book text to Canvas, and navigate chapters through the current book's catalog.

**Architecture:** Keep all browser interaction inside `WeReadController`. The controller uses the site's own layout toggle to enter vertical mode, identifies the chapter through `.readerCatalog_list_item_selected`, and clicks adjacent catalog entries for chapter navigation. `WeReadSource` first accepts ordinary rendered DOM paragraphs; when none exist, it screenshots the full rendered chapter Canvas and runs local Chinese OCR before passing text through the existing line parser.

**Tech Stack:** Python 3.10+, Playwright, RapidOCR ONNX Runtime, PySide6, unittest with mocked Playwright locators and OCR.

---

### Task 1: Local OCR runtime

**Files:**
- Modify: `requirements.txt`
- Modify: `README.md`

**Steps:**
1. Add `rapidocr-onnxruntime` to the project dependency list.
2. Install it only with `.venv\\Scripts\\python.exe -m pip install rapidocr-onnxruntime`.
3. Capture the real `.readerChapterContent canvas` element through Playwright and run OCR locally.
4. Confirm the result contains the visible chapter title and multiple body lines without network OCR services.

### Task 2: Current chapter metadata

**Files:**
- Modify: `weread_source.py`
- Modify: `tests/test_weread_source.py`

**Steps:**
1. Add failing tests for horizontal-layout detection and official vertical-layout toggle.
2. Add failing tests that map the selected catalog item's zero-based index to `chapter_id="catalog:<index>"` and use its title.
3. Implement the layout toggle and metadata extraction from rendered page/catalog DOM.
4. Keep DOM paragraph extraction as the preferred path for books that expose normal text.
5. Run the focused controller/source tests.

### Task 3: Canvas chapter OCR

**Files:**
- Modify: `weread_source.py`
- Modify: `tests/test_weread_source.py`

**Steps:**
1. Add failing tests for lazy OCR initialization, full Canvas screenshots, OCR result normalization, and empty-recognition errors.
2. Screenshot only the chapter Canvas into memory through Playwright.
3. Run RapidOCR locally and return recognized lines as `paragraphs` in visual reading order.
4. Cache only the final `WeReadChapter` units already owned by `WeReadSource`.
5. Run focused OCR tests with fake screenshot bytes and a fake OCR engine.

### Task 4: Catalog chapter navigation and restoration

**Files:**
- Modify: `weread_source.py`
- Modify: `main.py`
- Modify: `tests/test_weread_source.py`
- Modify: `tests/test_main.py`

**Steps:**
1. Add failing tests for adjacent catalog selection, first/last boundary handling, and waiting for selected index/title changes.
2. Replace text-button chapter navigation with clicks on adjacent `.readerCatalog_list_item` elements.
3. Extend chapter restoration to accept `catalog:<index>` alongside the book URL and click that catalog item after navigation.
4. Treat legacy synthetic chapter IDs as non-restorable and resume from WeRead's current selected catalog item.
5. Run controller and integration tests.

### Task 5: Real-page and regression verification

**Files:**
- Verify all application and test files.

**Steps:**
1. With the persisted LineRead profile, load the selected catalog chapter and confirm its real title is returned.
2. OCR the complete chapter Canvas and confirm substantial body text is produced.
3. Move to the next catalog item, confirm the title/id changes, then return to the original item.
4. Run `python -m unittest discover -s tests -v`.
5. Run `python -m py_compile` and `python -m pip check`.
6. Remove the diagnostic screenshot generated during investigation.
