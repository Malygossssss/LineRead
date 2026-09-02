import tempfile
import unittest
from pathlib import Path

from text_parser import TxtSource, parse_text


class ParseTextTests(unittest.TestCase):
    def test_cleans_and_preserves_non_empty_source_lines(self):
        text = "  第一段。\n\n  第二段！  第三段？\n第四段； "

        self.assertEqual(
            parse_text(text, max_chars=40),
            ["第一段。", "第二段！ 第三段？", "第四段；"],
        )

    def test_does_not_split_one_line_at_punctuation(self):
        self.assertEqual(
            parse_text("他沉默了……然后继续说。", max_chars=40),
            ["他沉默了……然后继续说。"],
        )

    def test_does_not_hard_split_a_long_source_line(self):
        text = "一二三四五六七八九十。"

        self.assertEqual(parse_text(text, max_chars=4), [text])


class TxtSourceTests(unittest.TestCase):
    def test_reads_utf8_txt(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "book.txt"
            path.write_text("你好。世界！\n第二行。", encoding="utf-8")

            self.assertEqual(TxtSource(path).get_units(), ["你好。世界！", "第二行。"])

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
