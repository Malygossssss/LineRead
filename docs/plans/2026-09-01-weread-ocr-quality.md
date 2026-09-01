# WeRead OCR Quality Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve WeRead Canvas recognition with tiled OCR, high-resolution screenshots, and targeted retries for uncertain lines.

**Architecture:** Capture one lossless device-scale Canvas PNG, split it into overlapping in-memory tiles, and normalize detections into full-image coordinates before deduplication. Retry only low-confidence line crops and keep the original whenever a retry is unavailable or less confident.

**Tech Stack:** Python 3.10+, Playwright, Pillow, RapidOCR ONNX Runtime, unittest.

---

### Task 1: Specify high-resolution capture and tiling

**Files:**
- Modify: `tests/test_weread_source.py`
- Modify: `weread_source.py`

1. Add a failing launch test asserting `device_scale_factor=2` for both bundled Chromium and Chrome fallback.
2. Add a failing Canvas test that supplies an in-memory PNG and asserts `screenshot(type="png", scale="device")`.
3. Add a failing test that verifies a tall image is split into 1600-pixel tiles with 160-pixel overlap.
4. Run `.venv\Scripts\python.exe -m unittest tests.test_weread_source -v` and confirm the new expectations fail.

### Task 2: Specify confidence retry and overlap deduplication

**Files:**
- Modify: `tests/test_weread_source.py`
- Modify: `weread_source.py`

1. Add a failing test where a low-confidence detection is replaced by a higher-confidence retry.
2. Add a failing test where a failed or lower-confidence retry retains the original text.
3. Add a failing test where detections from overlapping tiles share global geometry and collapse to one line.
4. Run the focused tests and confirm they fail for the intended missing behavior.

### Task 3: Implement the OCR pipeline

**Files:**
- Modify: `weread_source.py`
- Test: `tests/test_weread_source.py`

1. Add image/tile constants and a small immutable OCR-line value object.
2. Decode the screenshot with Pillow and yield overlapping in-memory tile images with their vertical offsets.
3. Parse OCR results with bounding boxes and confidence values, translate coordinates, and filter the header only in full-image coordinates.
4. Crop, enlarge, and retry low-confidence lines, accepting only a more confident result.
5. Sort and geometrically deduplicate overlapping detections.
6. Run `.venv\Scripts\python.exe -m unittest tests.test_weread_source -v` and confirm all focused tests pass.

### Task 4: Document and verify

**Files:**
- Modify: `requirements.txt`
- Modify: `README.md`

1. Declare Pillow as a direct dependency because application code imports it.
2. Update the WeRead documentation to describe high-resolution tiled local OCR and targeted retry behavior.
3. Run `.venv\Scripts\python.exe -m unittest discover -s tests -v` and confirm the complete suite passes.
4. Inspect `git diff --check` and `git diff` for formatting errors and unintended changes.
