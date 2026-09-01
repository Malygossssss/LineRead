# Reader Settings Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a lightweight right-click settings dialog that documents controls, edits font size and visible opacity, and persists configurable wheel modifier bindings.

**Architecture:** A standalone `SettingsDialog` owns only form presentation and validation. `DesktopReader` opens it from a context menu, applies accepted values, and persists the updated state through its existing callback. The config module normalizes shortcut data so older or damaged JSON remains compatible.

**Tech Stack:** Python 3.10+, PySide6 widgets, standard-library JSON and unittest.

---

### Task 1: Shortcut configuration schema

**Files:**
- Modify: `config.py`
- Modify: `config.json`
- Modify: `tests/test_config.py`

**Steps:**
1. Add failing tests for default shortcuts, valid shortcut round trips, unsupported modifiers, and conflicting modifier recovery.
2. Extend defaults and normalization with `font_wheel_modifier` and `opacity_wheel_modifier` values chosen from Ctrl, Shift, and Alt.
3. Run config tests and confirm backward compatibility.

### Task 2: Settings dialog

**Files:**
- Create: `settings_dialog.py`
- Create: `tests/test_settings_dialog.py`

**Steps:**
1. Add failing offscreen tests for initial values, value export, and duplicate shortcut rejection.
2. Implement a compact modal form with instructions, font spin box, opacity spin box, modifier selectors, Save, and Cancel.
3. Run dialog tests and confirm they pass.

### Task 3: Reader integration

**Files:**
- Modify: `reader_window.py`
- Modify: `tests/test_reader_window.py`

**Steps:**
1. Add tests for configured Ctrl/Shift/Alt wheel mappings, applying dialog settings, state export, and immediate persistence.
2. Add a right-click menu with Settings and Exit actions.
3. Apply accepted values to the active reader and save through the existing callback.
4. Run reader tests and confirm all existing interactions remain intact.

### Task 4: Documentation and verification

**Files:**
- Modify: `README.md`

**Steps:**
1. Document the right-click menu and configurable wheel modifiers.
2. Run the complete unittest suite and Python compilation checks.
3. Render the settings dialog with native Windows Qt and inspect its layout.
4. Run an end-to-end save/reload check for the shortcut configuration.
