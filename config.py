"""Defensive JSON configuration loading and saving."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping


CONFIG_PATH = Path(__file__).resolve().with_name("config.json")
ALLOWED_WHEEL_MODIFIERS = ("Ctrl", "Shift", "Alt")
MIN_FONT_SIZE = 10
MAX_FONT_SIZE = 40
MIN_VISIBLE_OPACITY = 0.2
MAX_VISIBLE_OPACITY = 1.0
DEFAULT_SHORTCUTS = {
    "font_wheel": "Ctrl",
    "opacity_wheel": "Shift",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "source": "txt",
    "file": "",
    "index": 0,
    "x": 400,
    "y": 50,
    "width": 900,
    "font_size": 14,
    "opacity": 0.85,
    "shortcuts": DEFAULT_SHORTCUTS.copy(),
    "weread": {
        "active_book_id": "",
        "books": {},
    },
}


def load_config(path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    """Load configuration, falling back safely when JSON is absent or bad."""

    config_path = Path(path)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return deepcopy(DEFAULT_CONFIG)

    if not isinstance(raw, dict):
        return deepcopy(DEFAULT_CONFIG)
    return normalize_config(raw)


def save_config(state: Mapping[str, Any], path: str | Path = CONFIG_PATH) -> None:
    """Atomically persist a normalized configuration mapping."""

    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    payload = json.dumps(normalize_config(state), ensure_ascii=False, indent=2)
    temp_path.write_text(payload + "\n", encoding="utf-8")
    temp_path.replace(config_path)


def normalize_config(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return only supported keys with safe values and documented bounds."""

    normalized = deepcopy(DEFAULT_CONFIG)

    source_value = raw.get("source")
    if source_value in ("txt", "weread"):
        normalized["source"] = source_value

    file_value = raw.get("file")
    if isinstance(file_value, str):
        normalized["file"] = file_value

    normalized["index"] = max(0, _integer(raw.get("index"), DEFAULT_CONFIG["index"]))
    normalized["x"] = _integer(raw.get("x"), DEFAULT_CONFIG["x"])
    normalized["y"] = _integer(raw.get("y"), DEFAULT_CONFIG["y"])

    width = _integer(raw.get("width"), DEFAULT_CONFIG["width"])
    normalized["width"] = width if 400 <= width <= 2400 else DEFAULT_CONFIG["width"]

    font_size = _number(raw.get("font_size"), DEFAULT_CONFIG["font_size"])
    normalized["font_size"] = int(
        max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, round(font_size)))
    )

    opacity = _number(raw.get("opacity"), DEFAULT_CONFIG["opacity"])
    normalized["opacity"] = round(
        max(MIN_VISIBLE_OPACITY, min(MAX_VISIBLE_OPACITY, opacity)), 2
    )

    shortcut_values = raw.get("shortcuts")
    if isinstance(shortcut_values, Mapping):
        font_wheel = _modifier(
            shortcut_values.get("font_wheel"),
            DEFAULT_SHORTCUTS["font_wheel"],
        )
        opacity_wheel = _modifier(
            shortcut_values.get("opacity_wheel"),
            DEFAULT_SHORTCUTS["opacity_wheel"],
        )
        if opacity_wheel == font_wheel:
            opacity_wheel = "Shift" if font_wheel != "Shift" else "Ctrl"
        normalized["shortcuts"] = {
            "font_wheel": font_wheel,
            "opacity_wheel": opacity_wheel,
        }

    weread_value = raw.get("weread")
    if isinstance(weread_value, Mapping):
        books_value = weread_value.get("books")
        books: dict[str, dict[str, Any]] = {}
        if isinstance(books_value, Mapping):
            for key, value in books_value.items():
                position = _weread_position(key, value)
                if position is not None:
                    books[position["book_id"]] = position

        active_book_id = _string(weread_value.get("active_book_id"))
        if active_book_id not in books:
            active_book_id = ""
        normalized["weread"] = {
            "active_book_id": active_book_id,
            "books": books,
        }
    return normalized


def _weread_position(key: Any, value: Any) -> dict[str, Any] | None:
    if not isinstance(key, str) or not isinstance(value, Mapping):
        return None

    book_id = _string(value.get("book_id")) or key.strip()
    chapter_id = _string(value.get("chapter_id"))
    if not book_id or not chapter_id:
        return None

    line_index = _integer(value.get("line_index"), 0)
    return {
        "book_id": book_id,
        "book_title": _string(value.get("book_title")),
        "chapter_id": chapter_id,
        "chapter_title": _string(value.get("chapter_title")),
        "chapter_url": _string(value.get("chapter_url")),
        "line_index": max(0, line_index),
    }


def _integer(value: Any, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    return value if isinstance(value, int) else fallback


def _number(value: Any, fallback: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float(fallback)
    return float(value)


def _modifier(value: Any, fallback: str) -> str:
    return value if value in ALLOWED_WHEEL_MODIFIERS else fallback


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
