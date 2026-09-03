# Page Prefetch Cache Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make normal WeRead page turns instant when background extraction has completed, while preserving the existing fallback and browser-thread safety.

**Architecture:** `WeReadIntegration` owns a session-only five-page sliding cache and serial speculative work on the existing Playwright executor. A cache hit emits the existing `page_ready` signal immediately, then synchronizes the browser and primes the next page in the background. `WeReadController` adds a bounded Canvas-hash OCR cache.

**Tech Stack:** Python 3.13, PySide6, `ThreadPoolExecutor`, Playwright, RapidOCR, `unittest`.

---

### Task 1: Specify speculative paging behavior

**Files:**
- Modify: `tests/test_main.py`
- Modify: `tests/test_weread_source.py`

**Step 1: Write failing integration tests**

Add a position-aware fake controller and verify that priming captures page 1 while
returning the browser to page 0, a forward cache hit emits synchronously, and the
old page can then be selected from memory in the reverse direction.

**Step 2: Write a failing OCR cache test**

Call current-page Canvas OCR twice with identical PNG bytes and assert that the OCR
engine runs only once.

**Step 3: Run focused tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_main.WeReadIntegrationTests tests.test_weread_source.WeReadControllerChapterTests -v`

Expected: FAIL because page priming and cross-call OCR caching do not exist.

### Task 2: Implement the sliding page cache

**Files:**
- Modify: `main.py`
- Modify: `tests/test_main.py`

**Step 1: Add cache state and priming**

Maintain cache generation, logical current index, browser index, attempted-prefetch
keys, and at most five `WeReadChapter` snapshots under a lock. Add
`prime_page_cache(chapter)` and schedule one speculative forward capture.

**Step 2: Serve cache hits and preserve fallback**

Check the adjacent logical index before submitting demand work. Emit cached results
immediately; otherwise retain the asynchronous worker path and recheck the cache
when the queued task starts.

**Step 3: Restore and continue prefetching**

After speculative capture, return the browser to its base index before publishing
the snapshot. After a cached or demanded page becomes current, synchronize the
browser and schedule the next forward prefetch.

**Step 4: Wire cache priming**

Connect startup and chapter-ready signals to `prime_page_cache` after the reader has
applied the corresponding snapshot. Prime synchronous open and book-switch results
inside their integration methods.

**Step 5: Run integration and reader tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_main tests.test_reader_window -v`

Expected: PASS.

### Task 3: Implement bounded OCR reuse and verify regressions

**Files:**
- Modify: `weread_source.py`
- Modify: `tests/test_weread_source.py`
- Modify: `README.md`

**Step 1: Add the Canvas OCR LRU**

Cache immutable OCR line tuples by SHA-256 Canvas bytes, refresh hits, and evict the
least-recently-used entry above the configured bound.

**Step 2: Document user-visible behavior**

Explain background next-page preparation, instant cached turns, bounded in-memory
history, and fallback behavior.

**Step 3: Compile and run the full suite**

Run: `.venv\Scripts\python.exe -m py_compile config.py main.py reader_window.py weread_source.py`

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: compilation and all tests pass.
