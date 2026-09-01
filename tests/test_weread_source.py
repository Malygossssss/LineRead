import os
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from weread_source import (
    WeReadController,
    WeReadError,
    WeReadSource,
    default_profile_dir,
)


def png_bytes(width=800, height=400):
    image = Image.new("RGB", (width, height), "white")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class FakeController:
    def __init__(self):
        self.payload = {
            "book_id": "book-1",
            "book_title": "测试书",
            "chapter_id": "chapter-1",
            "chapter_title": "第一章",
            "chapter_url": "https://weread.qq.com/web/reader/book-1-chapter-1",
            "paragraphs": ["第一句。第二句！", "这是较短的一段。"],
        }
        self.calls = []

    def get_current_chapter(self):
        self.calls.append("current")
        return self.payload

    def next_chapter(self):
        self.calls.append("next")
        self.payload = {
            **self.payload,
            "chapter_id": "chapter-2",
            "chapter_title": "第二章",
            "paragraphs": ["新章正文。"],
        }

    def previous_chapter(self):
        self.calls.append("previous")

    def restore_window(self):
        self.calls.append("restore")

    def close(self):
        self.calls.append("close")


class WeReadSourceTests(unittest.TestCase):
    def test_loads_and_caches_display_units_from_rendered_paragraphs(self):
        controller = FakeController()
        source = WeReadSource(controller)

        chapter = source.load_current_chapter()

        self.assertEqual(chapter.book_id, "book-1")
        self.assertEqual(chapter.chapter_title, "第一章")
        self.assertEqual(
            chapter.units,
            ("第一句。", "第二句！", "这是较短的一段。"),
        )
        self.assertIs(source.cached_chapter, chapter)

    def test_next_chapter_delegates_then_refreshes_cache(self):
        controller = FakeController()
        source = WeReadSource(controller)

        chapter = source.next_chapter()

        self.assertEqual(controller.calls, ["next", "current"])
        self.assertEqual(chapter.chapter_id, "chapter-2")
        self.assertEqual(chapter.units, ("新章正文。",))

    def test_missing_book_title_or_body_is_rejected(self):
        controller = FakeController()
        controller.payload["book_title"] = ""
        source = WeReadSource(controller)

        with self.assertRaisesRegex(WeReadError, "书名"):
            source.load_current_chapter()

        controller.payload["book_title"] = "测试书"
        controller.payload["paragraphs"] = []
        with self.assertRaisesRegex(WeReadError, "没有可阅读"):
            source.load_current_chapter()

    def test_missing_dom_ids_get_stable_fallback_ids(self):
        controller = FakeController()
        controller.payload["book_id"] = ""
        controller.payload["chapter_id"] = ""
        source = WeReadSource(controller)

        first = source.load_current_chapter()
        second = source.load_current_chapter()

        self.assertEqual(first.book_id, second.book_id)
        self.assertEqual(first.chapter_id, second.chapter_id)
        self.assertTrue(first.book_id.startswith("book-"))

    def test_profile_path_can_be_overridden_without_global_python_changes(self):
        with patch.dict(os.environ, {"LINEREAD_WEREAD_PROFILE": "D:/profiles/weread"}):
            self.assertEqual(str(default_profile_dir()), "D:\\profiles\\weread")


class WeReadControllerLaunchTests(unittest.TestCase):
    @staticmethod
    def make_page(url):
        page = Mock()
        page.url = url
        page.is_closed.return_value = False
        return page

    def test_reuses_chromium_startup_blank_page(self):
        blank_page = self.make_page("about:blank")
        context = Mock()
        context.pages = [blank_page]
        controller = WeReadController("D:/profiles/weread")
        controller._context = context

        selected = controller._select_browser_page()

        self.assertIs(selected, blank_page)
        context.new_page.assert_not_called()
        blank_page.close.assert_not_called()

    def test_prefers_existing_weread_page_and_closes_surplus_blank_page(self):
        blank_page = self.make_page("about:blank")
        weread_page = self.make_page("https://weread.qq.com/web/reader/book-1")
        context = Mock()
        context.pages = [blank_page, weread_page]
        controller = WeReadController("D:/profiles/weread")
        controller._context = context

        selected = controller._select_browser_page()

        self.assertIs(selected, weread_page)
        blank_page.close.assert_called_once_with()
        weread_page.close.assert_not_called()
        context.new_page.assert_not_called()

    def test_creates_page_only_when_context_has_no_open_page(self):
        created_page = self.make_page("about:blank")
        context = Mock()
        context.pages = []
        context.new_page.return_value = created_page
        controller = WeReadController("D:/profiles/weread")
        controller._context = context

        selected = controller._select_browser_page()

        self.assertIs(selected, created_page)
        context.new_page.assert_called_once_with()

    def test_spawn_unknown_falls_back_to_installed_chrome(self):
        expected_context = object()
        chromium = Mock()
        chromium.launch_persistent_context.side_effect = [
            RuntimeError("spawn UNKNOWN"),
            expected_context,
        ]
        controller = WeReadController("D:/profiles/weread")
        controller._playwright = SimpleNamespace(chromium=chromium)

        context = controller._launch_persistent_context()

        self.assertIs(context, expected_context)
        self.assertEqual(controller.browser_channel, "chrome")
        first_call, second_call = chromium.launch_persistent_context.call_args_list
        self.assertNotIn("channel", first_call.kwargs)
        self.assertEqual(second_call.kwargs["channel"], "chrome")
        self.assertEqual(first_call.kwargs["device_scale_factor"], 2)
        self.assertEqual(second_call.kwargs["device_scale_factor"], 2)

    def test_missing_bundled_executable_falls_back_to_installed_chrome(self):
        expected_context = object()
        chromium = Mock()
        chromium.launch_persistent_context.side_effect = [
            RuntimeError(
                "BrowserType.launch_persistent_context: Executable doesn't exist at "
                "C:/Users/test/AppData/Local/ms-playwright/chromium/chrome.exe"
            ),
            expected_context,
        ]
        controller = WeReadController("D:/profiles/weread")
        controller._playwright = SimpleNamespace(chromium=chromium)

        context = controller._launch_persistent_context()

        self.assertIs(context, expected_context)
        self.assertEqual(controller.browser_channel, "chrome")
        self.assertEqual(
            chromium.launch_persistent_context.call_args_list[1].kwargs["channel"],
            "chrome",
        )

    def test_unrelated_launch_error_does_not_hide_behind_chrome_fallback(self):
        chromium = Mock()
        chromium.launch_persistent_context.side_effect = RuntimeError(
            "user data directory is already in use"
        )
        controller = WeReadController("D:/profiles/weread")
        controller._playwright = SimpleNamespace(chromium=chromium)

        with self.assertRaisesRegex(RuntimeError, "already in use"):
            controller._launch_persistent_context()

        chromium.launch_persistent_context.assert_called_once()

    def test_failed_fallback_reports_both_browser_errors(self):
        chromium = Mock()
        chromium.launch_persistent_context.side_effect = [
            RuntimeError("spawn UNKNOWN"),
            RuntimeError("Chrome executable does not exist"),
        ]
        controller = WeReadController("D:/profiles/weread")
        controller._playwright = SimpleNamespace(chromium=chromium)

        with self.assertRaises(WeReadError) as raised:
            controller._launch_persistent_context()

        self.assertIn("spawn UNKNOWN", str(raised.exception))
        self.assertIn("Chrome executable does not exist", str(raised.exception))


class WeReadControllerChapterTests(unittest.TestCase):
    def make_connected_controller(self):
        controller = WeReadController("D:/profiles/weread")
        page = Mock()
        page.is_closed.return_value = False
        page.url = "https://weread.qq.com/web/reader/book-1"
        controller._page = page
        controller._context = Mock()
        return controller, page

    def test_current_canvas_chapter_uses_catalog_metadata_and_ocr_lines(self):
        controller, page = self.make_connected_controller()
        page.evaluate.return_value = {
            "book_id": "book-1",
            "book_title": "测试书",
            "chapter_id": "",
            "chapter_title": "第七章",
            "chapter_url": page.url,
            "paragraphs": [],
        }

        with (
            patch.object(controller, "_ensure_vertical_layout") as ensure_vertical,
            patch.object(
                controller,
                "_catalog_position",
                return_value={
                    "index": 7,
                    "count": 10,
                    "chapter_id": "catalog:stable-seven",
                    "title": "第七章",
                },
            ),
            patch.object(
                controller,
                "_ocr_current_canvas",
                return_value=["正文第一行。", "正文第二行。"],
            ) as ocr,
        ):
            payload = controller.get_current_chapter()

        ensure_vertical.assert_called_once()
        ocr.assert_called_once()
        self.assertEqual(payload["chapter_id"], "catalog:stable-seven")
        self.assertEqual(payload["chapter_title"], "第七章")
        self.assertEqual(payload["paragraphs"], ["正文第一行。", "正文第二行。"])

    def test_ocr_ignores_canvas_header_navigation(self):
        controller, page = self.make_connected_controller()
        canvas = Mock()
        canvas.screenshot.return_value = png_bytes()
        locator = Mock()
        locator.count.return_value = 1
        locator.first = canvas
        page.locator.return_value = locator
        controller._ocr_engine = Mock(
            return_value=(
                [
                    ([[0, 48], [180, 48], [180, 94], [0, 94]], "测试书", 0.99),
                    ([[16, 170], [700, 170], [700, 216], [16, 216]], "正文第一行。", 0.98),
                    ([[16, 254], [700, 254], [700, 298], [16, 298]], "正文第二行。", 0.97),
                ],
                [0.1, 0.1, 0.1],
            )
        )

        lines = controller._ocr_current_canvas()

        self.assertEqual(lines, ["正文第一行。", "正文第二行。"])
        canvas.screenshot.assert_called_once_with(type="png", scale="device")
        self.assertEqual(controller._ocr_engine.call_args.kwargs["unclip_ratio"], 1.8)

    def test_ocr_splits_tall_canvas_and_deduplicates_overlapping_lines(self):
        controller, page = self.make_connected_controller()
        canvas = Mock()
        canvas.screenshot.return_value = png_bytes(width=800, height=3000)
        locator = Mock()
        locator.count.return_value = 1
        locator.first = canvas
        page.locator.return_value = locator
        tile_heights = []

        def recognize(tile_png, **kwargs):
            self.assertEqual(kwargs["unclip_ratio"], 1.8)
            with Image.open(BytesIO(tile_png)) as tile:
                tile_heights.append(tile.height)
            if len(tile_heights) == 1:
                return (
                    [
                        ([[10, 200], [500, 200], [500, 240], [10, 240]], "第一行。", 0.98),
                        ([[10, 1500], [500, 1500], [500, 1540], [10, 1540]], "重叠行。", 0.91),
                    ],
                    None,
                )
            return (
                [
                    ([[10, 60], [500, 60], [500, 100], [10, 100]], "重叠行。", 0.96),
                    ([[10, 1000], [500, 1000], [500, 1040], [10, 1040]], "最后一行。", 0.97),
                ],
                None,
            )

        controller._ocr_engine = Mock(side_effect=recognize)

        lines = controller._ocr_current_canvas()

        self.assertEqual(tile_heights, [1600, 1560])
        self.assertEqual(lines, ["第一行。", "重叠行。", "最后一行。"])

    def test_ocr_retries_and_replaces_a_low_confidence_line(self):
        controller, page = self.make_connected_controller()
        canvas = Mock()
        canvas.screenshot.return_value = png_bytes()
        locator = Mock()
        locator.count.return_value = 1
        locator.first = canvas
        page.locator.return_value = locator
        controller._ocr_engine = Mock(
            side_effect=[
                (
                    [([[20, 180], [500, 180], [500, 230], [20, 230]], "一个错宇。", 0.61)],
                    None,
                ),
                (
                    [([[10, 10], [700, 10], [700, 110], [10, 110]], "一个错字。", 0.96)],
                    None,
                ),
            ]
        )

        lines = controller._ocr_current_canvas()

        self.assertEqual(lines, ["一个错字。"])
        self.assertEqual(controller._ocr_engine.call_count, 2)

    def test_ocr_keeps_original_when_retry_has_lower_confidence(self):
        controller, page = self.make_connected_controller()
        canvas = Mock()
        canvas.screenshot.return_value = png_bytes()
        locator = Mock()
        locator.count.return_value = 1
        locator.first = canvas
        page.locator.return_value = locator
        controller._ocr_engine = Mock(
            side_effect=[
                (
                    [([[20, 180], [500, 180], [500, 230], [20, 230]], "保留原文。", 0.61)],
                    None,
                ),
                (
                    [([[10, 10], [700, 10], [700, 110], [10, 110]], "更差结果。", 0.55)],
                    None,
                ),
            ]
        )

        lines = controller._ocr_current_canvas()

        self.assertEqual(lines, ["保留原文。"])
        self.assertEqual(controller._ocr_engine.call_count, 2)

    def test_next_and_previous_chapter_use_adjacent_catalog_items(self):
        controller, _ = self.make_connected_controller()

        with (
            patch.object(
                controller,
                "_catalog_position",
                side_effect=[{"index": 7, "count": 10}, {"index": 7, "count": 10}],
            ),
            patch.object(controller, "_select_catalog_index") as select,
        ):
            controller.next_chapter()
            controller.previous_chapter()

        self.assertEqual([call.args[0] for call in select.call_args_list], [8, 6])

    def test_catalog_navigation_reports_real_book_boundaries(self):
        controller, _ = self.make_connected_controller()

        with patch.object(
            controller,
            "_catalog_position",
            return_value={"index": 9, "count": 10},
        ):
            with self.assertRaisesRegex(WeReadError, "最后一章"):
                controller.next_chapter()

    def test_restore_catalog_chapter_clicks_saved_stable_id(self):
        controller, page = self.make_connected_controller()

        with patch.object(controller, "_select_catalog_chapter") as select:
            controller.open_chapter_url(page.url, "catalog:stable-twelve")

        page.goto.assert_called_once_with(page.url, wait_until="domcontentloaded")
        select.assert_called_once_with("catalog:stable-twelve")

    def test_catalog_id_is_stable_when_front_matter_list_changes(self):
        controller, page = self.make_connected_controller()
        page.evaluate.side_effect = [
            {
                "items": [
                    {"title": "扉页", "level": 1, "selected": False},
                    {"title": "三十而立", "level": 1, "selected": False},
                    {"title": "四", "level": 2, "selected": True},
                ]
            },
            {
                "items": [
                    {"title": "版权信息", "level": 1, "selected": False},
                    {"title": "序", "level": 1, "selected": False},
                    {"title": "三十而立", "level": 1, "selected": False},
                    {"title": "四", "level": 2, "selected": True},
                ]
            },
        ]

        first = controller._catalog_position()
        second = controller._catalog_position()

        self.assertNotEqual(first["index"], second["index"])
        self.assertEqual(first["chapter_id"], second["chapter_id"])
        self.assertEqual(first["title"], "四")


if __name__ == "__main__":
    unittest.main()
