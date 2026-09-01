import json
import tempfile
import unittest
from pathlib import Path

from config import DEFAULT_CONFIG, load_config, save_config


class ConfigTests(unittest.TestCase):
    def test_missing_config_returns_fresh_defaults(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "missing.json"

            loaded = load_config(path)

            self.assertEqual(loaded, DEFAULT_CONFIG)
            self.assertIsNot(loaded, DEFAULT_CONFIG)
            self.assertIsNot(loaded["shortcuts"], DEFAULT_CONFIG["shortcuts"])

    def test_corrupt_config_returns_defaults(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "config.json"
            path.write_text("{broken", encoding="utf-8")

            self.assertEqual(load_config(path), DEFAULT_CONFIG)

    def test_invalid_values_are_sanitized(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "file": 123,
                        "index": -4,
                        "x": "bad",
                        "y": 80,
                        "width": 10,
                        "font_size": 99,
                        "opacity": 4,
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_config(path)

            self.assertEqual(loaded["file"], "")
            self.assertEqual(loaded["index"], 0)
            self.assertEqual(loaded["x"], DEFAULT_CONFIG["x"])
            self.assertEqual(loaded["y"], 80)
            self.assertEqual(loaded["width"], DEFAULT_CONFIG["width"])
            self.assertEqual(loaded["font_size"], 40)
            self.assertEqual(loaded["opacity"], 1.0)
            self.assertEqual(
                loaded["shortcuts"],
                {"font_wheel": "Ctrl", "opacity_wheel": "Shift"},
            )

    def test_save_and_load_round_trip(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "nested" / "config.json"
            state = {
                "file": "D:/books/example.txt",
                "index": 1837,
                "x": 400,
                "y": 50,
                "width": 900,
                "font_size": 14,
                "opacity": 0.85,
                "shortcuts": {
                    "font_wheel": "Alt",
                    "opacity_wheel": "Ctrl",
                },
            }

            save_config(state, path)

            self.assertEqual(load_config(path), state)
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_invalid_and_conflicting_shortcuts_are_repaired(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "shortcuts": {
                            "font_wheel": "Shift",
                            "opacity_wheel": "Shift",
                        }
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_config(path)

            self.assertEqual(loaded["shortcuts"]["font_wheel"], "Shift")
            self.assertEqual(loaded["shortcuts"]["opacity_wheel"], "Ctrl")

    def test_unsupported_shortcut_modifier_uses_default(self):
        normalized = load_config(Path("definitely-missing-config.json"))
        self.assertEqual(normalized["shortcuts"], DEFAULT_CONFIG["shortcuts"])

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "shortcuts": {
                            "font_wheel": "Win",
                            "opacity_wheel": "Alt",
                        }
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                load_config(path)["shortcuts"],
                {"font_wheel": "Ctrl", "opacity_wheel": "Alt"},
            )


if __name__ == "__main__":
    unittest.main()
