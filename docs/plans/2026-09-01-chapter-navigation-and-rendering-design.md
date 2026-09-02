# Chapter Navigation and Rendering Design

## Problem

The reader already extracts the WeRead catalog, but only exposes adjacent chapter
navigation. Chapter changes run synchronously on the Qt UI thread, so the floating
window cannot repaint while OCR is running. Display-ready OCR rows are also passed
through punctuation-aware parsing, which changes the source line boundaries. The
Canvas discovery loop can finish after a short stable period at a temporary bottom,
before a long chapter has appended all of its lazily rendered content.

## Selected design

Attach a typed catalog snapshot to every cached `WeReadChapter`. Add a searchable Qt
chapter-selection dialog that filters the snapshot locally, highlights the current
chapter, and submits the stable catalog chapter id. Keep adjacent navigation and
direct selection on the same single Playwright worker thread.

Treat source line boundaries as authoritative. TXT input preserves each non-empty
physical line, and WeRead preserves every DOM/OCR line without punctuation or fixed
width splitting.

Run all chapter changes asynchronously. The reader immediately displays
“文本渲染中…”, blocks repeated navigation, and keeps the previous cached chapter in
memory. A successful worker result atomically replaces the chapter; a failure restores
the prior line and displays the error.

Strengthen long Canvas capture by tracking rendered extent and scroll extent, actively
continuing discovery after reaching a temporary bottom, and requiring a longer stable
completion window whose counters reset whenever pixels, text, Canvas count, or scroll
extent changes. Completion must represent a stable chapter end rather than three fast
empty scans at an intermediate lazy-load boundary.

## Verification

Tests cover catalog propagation and direct selection, searchable/current-chapter UI
state, physical-line parsing, preservation of OCR rows, delayed Canvas growth after a
temporary bottom, async chapter success/failure, and loading-state repaint behavior.
The full unit suite, bytecode compilation, and `git diff --check` provide regression
coverage.
