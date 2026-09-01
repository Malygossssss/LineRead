import tempfile
import unittest
from pathlib import Path

from text_parser import TxtSource, parse_text


class ParseTextTests(unittest.TestCase):
    def test_cleans_lines_and_splits_on_strong_punctuation(self):
        text = "  第一段。\n\n  第二段！  第三段？\n第四段； "

        self.assertEqual(
            parse_text(text, max_chars=40),
            ["第一段。", "第二段！", "第三段？", "第四段；"],
        )

    def test_keeps_chinese_ellipsis_with_preceding_sentence(self):
        self.assertEqual(
            parse_text("他沉默了……然后继续说。", max_chars=40),
            ["他沉默了……", "然后继续说。"],
        )

    def test_long_sentence_uses_weak_punctuation_without_over_splitting(self):
        text = "甲乙丙丁，戊己庚辛，壬癸子丑，寅卯辰巳。"

        self.assertEqual(
            parse_text(text, max_chars=12),
            ["甲乙丙丁，戊己庚辛，", "壬癸子丑，寅卯辰巳。"],
        )

    def test_oversized_fragment_is_hard_split(self):
        units = parse_text("一二三四五六七八九十。", max_chars=4)

        self.assertEqual(units, ["一二三四", "五六七八", "九十。"])
        self.assertTrue(all(len(unit) <= 4 for unit in units))


class TxtSourceTests(unittest.TestCase):
    def test_reads_utf8_txt(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "book.txt"
            path.write_text("你好。世界！", encoding="utf-8")

            self.assertEqual(TxtSource(path).get_units(), ["你好。", "世界！"])

    def test_rejects_empty_file(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "empty.txt"
            path.write_text(" \n\n ", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "没有可阅读内容"):
                TxtSource(path).get_units()

    def test_rejects_non_utf8_file(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "legacy.txt"
            path.write_bytes("中文".encode("gbk"))

            with self.assertRaisesRegex(ValueError, "UTF-8"):
                TxtSource(path).get_units()


if __name__ == "__main__":
    unittest.main()
