# Desktop Line Reader Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a directly runnable Windows PySide6 MVP that reads local UTF-8 TXT files in a draggable, always-on-top, hover-revealed single-line window and restores reading state.

**Architecture:** Keep content acquisition separate from presentation through `ReaderSource` and `TxtSource`. Parse source text into punctuation-aware units before constructing `DesktopReader`, while the window owns only navigation and desktop interaction. Persist one compact JSON state atomically through a dedicated configuration module.

**Tech Stack:** Python 3.10+, PySide6, standard-library `json`, `pathlib`, `re`, and `unittest`.

---

### Task 1: Text source and parser

**Files:**
- Create: `text_parser.py`
- Create: `tests/test_text_parser.py`

**Steps:**
1. Add tests for blank-line cleanup, strong/weak Chinese punctuation, ellipses, oversized fragments, empty files, and invalid UTF-8.
2. Run `python -m unittest tests.test_text_parser -v` and confirm the tests initially fail.
3. Implement `ReaderSource`, `TxtSource`, and punctuation-aware `parse_text` with a configurable maximum character count.
4. Run the parser tests and confirm they pass.

### Task 2: Configuration persistence

**Files:**
- Create: `config.py`
- Create: `tests/test_config.py`

**Steps:**
1. Add tests for missing/corrupt config fallback, value sanitization, and JSON round-trip persistence.
2. Run `python -m unittest tests.test_config -v` and confirm the tests initially fail.
3. Implement defaults, defensive loading, normalization, and atomic saving.
4. Run the config tests and confirm they pass.

### Task 3: Floating reader window

**Files:**
- Create: `reader_window.py`
- Create: `tests/test_reader_window.py`

**Steps:**
1. Add offscreen Qt tests for window flags, navigation bounds, font adjustment, opacity adjustment, and single-line label behavior.
2. Run `python -m unittest tests.test_reader_window -v` and confirm the tests initially fail.
3. Implement `DesktopReader` with frameless/tool/topmost flags, hover opacity, wheel modifiers, drag movement, clamped state, and close-time persistence callback.
4. Run the window tests and confirm they pass.

### Task 4: Application entry point and packaging

**Files:**
- Create: `main.py`
- Create: `requirements.txt`
- Create: `README.md`
- Create: `config.json`

**Steps:**
1. Implement startup config resolution, optional remembered-file reuse, TXT file picker, user-facing load errors, and window construction.
2. Document install, launch, controls, configuration location, and UTF-8 constraint.
3. Add the minimal PySide6 dependency and a safe default config file.

### Task 5: End-to-end verification

**Files:**
- Verify all project files.

**Steps:**
1. Run `python -m unittest discover -s tests -v`.
2. Run `python -m py_compile main.py reader_window.py text_parser.py config.py`.
3. Launch with `QT_QPA_PLATFORM=offscreen` using a temporary TXT and automatically close after startup to verify wiring and state persistence.
4. Review the final directory and configuration output against all ten acceptance criteria.
