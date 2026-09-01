# WeRead OCR Quality Design

## Problem

The current Canvas path sends one full-chapter screenshot through RapidOCR and
keeps only the recognized text. Long images may lose small text during OCR
resizing, overlapping recognition is unavailable, and uncertain lines cannot be
retried because their confidence scores are discarded.

## Selected design

Launch the WeRead browser at a device scale factor of 2 and explicitly capture
the Canvas at device scale. Decode the PNG in memory, divide it into 1600-pixel
vertical tiles with 160 pixels of overlap, and OCR every tile independently.
Translate each detected box back into full-image coordinates, sort the results,
and collapse geometrically overlapping detections while retaining the result
with the greater confidence.

For a detected line below confidence 0.82, crop the line with padding, enlarge
it by 2x with Lanczos resampling, and run one additional OCR pass. Replace the
original text only when the retry returns a non-empty result with higher
confidence. A retry failure retains the original line instead of failing the
chapter.

All image processing remains local and in memory. DOM-based chapters remain
unchanged.

## Alternatives considered

- Scroll the page and capture viewport-sized slices: avoids a large image in
  memory, but mutates page state and is more susceptible to sticky controls and
  lazy rendering.
- Replace the OCR model first: may improve recognition, but does not address
  long-image downscaling or missing confidence handling.
- Re-OCR the entire tile at several preprocessing settings: improves recall at
  substantially higher latency; targeted line retries provide a better initial
  trade-off.

## Verification

Unit tests cover device-scale browser launch and screenshot options, exact tile
boundaries and overlap, global-coordinate duplicate removal, successful and
failed low-confidence retries, header filtering, and the existing DOM/Canvas
fallback behavior. The complete unittest suite guards the desktop reader and
configuration paths.
