import os
import unittest
from base64 import b64encode
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from weread_source import (
    _OcrLine,
    _merge_ocr_rows,
    _positioned_rows,
    _reconcile_visual_rows,
    WeReadCatalogEntry,
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


def png_data_url(width=800, height=400):
    payload = b64encode(png_bytes(width=width, height=height)).decode("ascii")
    return f"data:image/png;base64,{payload}"


class FakeController:
    def __init__(self):
        self.payload = {
            "book_id": "book-1",
            "book_title": "测试书",
            "chapter_id": "chapter-1",
            "chapter_title": "第一章",
            "chapter_url": "https://weread.qq.com/web/reader/book-1-chapter-1",
            "paragraphs": ["第一句。第二句！", "这是较短的一段。"],
            "catalog_index": 0,
            "catalog": [
                {
                    "chapter_id": "chapter-1",
                    "title": "第一章",
                    "level": 1,
                },
                {
                    "chapter_id": "chapter-2",
                    "title": "第二章",
                    "level": 1,
                },
            ],
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

    def next_page(self):
        self.calls.append("next_page")
        self.payload = {
            **self.payload,
            "paragraphs": ["下一页第一行。", "下一页第二行。"],
        }

    def previous_page(self):
        self.calls.append("previous_page")
        self.payload = {
            **self.payload,
            "paragraphs": ["上一页第一行。", "上一页第二行。"],
        }

    def select_chapter(self, chapter_id):
        self.calls.append(("select", chapter_id))
        self.payload = {
            **self.payload,
            "chapter_id": chapter_id,
            "chapter_title": "第二章",
            "catalog_index": 1,
            "paragraphs": ["直接选中的正文。"],
        }

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
            ("第一句。第二句！", "这是较短的一段。"),
        )
        self.assertEqual(chapter.catalog_index, 0)
        self.assertEqual(
            chapter.catalog,
            (
                WeReadCatalogEntry("chapter-1", "第一章", 1),
                WeReadCatalogEntry("chapter-2", "第二章", 1),
            ),
        )
        self.assertIs(source.cached_chapter, chapter)

    def test_next_chapter_delegates_then_refreshes_cache(self):
        controller = FakeController()
        source = WeReadSource(controller)

        chapter = source.next_chapter()

        self.assertEqual(controller.calls, ["next", "current"])
        self.assertEqual(chapter.chapter_id, "chapter-2")
        self.assertEqual(chapter.units, ("新章正文。",))

    def test_select_chapter_delegates_stable_id_then_refreshes_cache(self):
        controller = FakeController()
        source = WeReadSource(controller)

        chapter = source.select_chapter("chapter-2")

        self.assertEqual(controller.calls, [("select", "chapter-2"), "current"])
        self.assertEqual(chapter.chapter_id, "chapter-2")
        self.assertEqual(chapter.units, ("直接选中的正文。",))

    def test_page_turns_delegate_then_refresh_the_current_page(self):
        controller = FakeController()
        source = WeReadSource(controller)

        next_page = source.next_page()
        previous_page = source.previous_page()

        self.assertEqual(
            controller.calls,
            ["next_page", "current", "previous_page", "current"],
        )
        self.assertEqual(next_page.units[0], "下一页第一行。")
        self.assertEqual(previous_page.units[-1], "上一页第二行。")

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

    def test_readiness_state_reports_reader_from_url_without_dom_probe(self):
        controller, page = self.make_connected_controller()

        state = controller.readiness_state()

        self.assertEqual(state, "reader")
        page.evaluate.assert_not_called()

    def test_readiness_state_distinguishes_login_from_book_selection(self):
        controller, page = self.make_connected_controller()
        page.url = "https://weread.qq.com/"
        page.evaluate.side_effect = ["login", "book"]

        self.assertEqual(controller.readiness_state(), "login")
        self.assertEqual(controller.readiness_state(), "book")

    def test_selecting_already_current_hidden_catalog_item_is_a_noop(self):
        controller, page = self.make_connected_controller()
        items = Mock()
        target = Mock()
        items.count.return_value = 3
        items.nth.return_value = target
        target.evaluate.return_value = True
        page.locator.return_value = items

        controller._select_catalog_index(1)

        target.is_visible.assert_not_called()
        target.click.assert_not_called()
        page.wait_for_function.assert_not_called()

    def test_chapter_render_wait_uses_a_bounded_page_condition(self):
        controller, page = self.make_connected_controller()

        controller._wait_for_chapter_render()

        page.wait_for_function.assert_called_once()
        self.assertEqual(page.wait_for_function.call_args.kwargs["timeout"], 20_000)

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
            patch.object(controller, "_ensure_horizontal_layout") as ensure_horizontal,
            patch.object(
                controller,
                "_catalog_position",
                return_value={
                    "index": 7,
                    "count": 10,
                    "chapter_id": "catalog:stable-seven",
                    "title": "第七章",
                    "entries": [
                        {
                            "chapter_id": "catalog:stable-seven",
                            "title": "第七章",
                            "level": 1,
                        }
                    ],
                },
            ),
            patch.object(
                controller,
                "_read_current_page_visual_rows",
                return_value=["正文第一行。", "正文第二行。"],
            ) as ocr,
        ):
            payload = controller.get_current_chapter()

        ensure_horizontal.assert_called_once()
        ocr.assert_called_once()
        self.assertEqual(payload["chapter_id"], "catalog:stable-seven")
        self.assertEqual(payload["chapter_title"], "第七章")
        self.assertEqual(payload["paragraphs"], ["正文第一行。", "正文第二行。"])

    def test_canvas_chapter_uses_visual_rows_even_when_dom_paragraphs_exist(self):
        controller, page = self.make_connected_controller()
        page.evaluate.return_value = {
            "book_id": "book-1",
            "book_title": "测试书",
            "chapter_id": "",
            "chapter_title": "第六章",
            "chapter_url": page.url,
            "has_canvas": True,
            "paragraphs": ["一个跨越页面多行的 DOM 段落。第二句。第三句。"],
        }

        with (
            patch.object(controller, "_ensure_horizontal_layout"),
            patch.object(
                controller,
                "_catalog_position",
                return_value={
                    "index": 6,
                    "count": 10,
                    "chapter_id": "catalog:stable-six",
                    "title": "第六章",
                    "entries": [],
                },
            ),
            patch.object(
                controller,
                "_read_current_page_visual_rows",
                return_value=["页面第一行。", "页面第二行。"],
            ) as ocr,
        ):
            payload = controller.get_current_chapter()

        ocr.assert_called_once_with()
        self.assertEqual(payload["paragraphs"], ["页面第一行。", "页面第二行。"])

    def test_positioned_text_renderer_overrides_semantic_dom_paragraphs(self):
        controller, page = self.make_connected_controller()
        page.evaluate.return_value = {
            "book_id": "book-1",
            "book_title": "测试书",
            "chapter_id": "",
            "chapter_title": "第六章",
            "chapter_url": page.url,
            "has_canvas": False,
            "uses_visual_renderer": True,
            "paragraphs": ["这是一个跨越多行的完整段落。"],
        }

        with (
            patch.object(controller, "_ensure_horizontal_layout"),
            patch.object(
                controller,
                "_catalog_position",
                return_value={
                    "index": 6,
                    "count": 10,
                    "chapter_id": "catalog:stable-six",
                    "title": "第六章",
                    "entries": [],
                },
            ),
            patch.object(
                controller,
                "_read_current_page_visual_rows",
                return_value=["页面第一行。", "页面第二行。"],
            ) as visual_rows,
        ):
            payload = controller.get_current_chapter()

        visual_rows.assert_called_once_with()
        self.assertEqual(payload["paragraphs"], ["页面第一行。", "页面第二行。"])

    def test_current_page_canvas_capture_never_scrolls_the_chapter(self):
        controller, page = self.make_connected_controller()
        first_canvas = Mock()
        first_canvas.evaluate.return_value = png_data_url(height=420)
        second_canvas = Mock()
        second_canvas.evaluate.return_value = png_data_url(height=421)
        canvases = Mock()
        canvases.count.return_value = 2
        canvases.first = first_canvas
        canvases.nth.return_value = second_canvas
        page.locator.return_value = canvases
        controller._ocr_engine = Mock(
            side_effect=[
                (
                    [([[10, 90], [500, 90], [500, 130], [10, 130]], "左页。", 0.98)],
                    None,
                ),
                (
                    [([[10, 90], [500, 90], [500, 130], [10, 130]], "右页。", 0.98)],
                    None,
                ),
            ]
        )

        rows = controller._ocr_current_page_canvases()

        self.assertEqual(rows, ["左页。", "右页。"])
        page.locator.assert_called_once_with(".renderTargetContainer canvas")
        page.evaluate.assert_not_called()

    def test_ensure_horizontal_layout_uses_the_layout_control(self):
        controller, page = self.make_connected_controller()
        horizontal = Mock()
        horizontal.count.return_value = 0
        toggle = Mock()
        toggle.count.return_value = 1
        toggle.first = toggle

        def locator(selector):
            if selector == ".wr_horizontalReader":
                return horizontal
            if selector == ".readerControls_item.showBookReviews + .readerControls_item":
                return toggle
            return Mock()

        page.locator.side_effect = locator

        controller._ensure_horizontal_layout()

        toggle.click.assert_called_once_with()
        page.wait_for_function.assert_called_once()
        self.assertEqual(page.wait_for_function.call_args.kwargs["timeout"], 10_000)

    def test_page_turn_clicks_public_control_and_waits_for_new_render(self):
        controller, page = self.make_connected_controller()
        button = Mock()
        button.count.return_value = 1
        button.first = button
        button.is_visible.return_value = True
        button.is_enabled.return_value = True
        page.locator.return_value = button

        with (
            patch.object(controller, "_ensure_horizontal_layout"),
            patch.object(controller, "_page_signature", return_value="page-before"),
        ):
            controller.next_page()

        page.locator.assert_called_with(
            ".renderTarget_pager_button.renderTarget_pager_button_right"
        )
        button.click.assert_called_once_with()
        page_change_wait = page.wait_for_function.call_args_list[0]
        self.assertEqual(page_change_wait.args[1], "page-before")
        self.assertEqual(page_change_wait.kwargs["timeout"], 20_000)

    def test_page_turn_reports_unavailable_boundary(self):
        controller, page = self.make_connected_controller()
        button = Mock()
        button.count.return_value = 1
        button.first = button
        button.is_visible.return_value = True
        button.is_enabled.return_value = False
        page.locator.return_value = button

        with patch.object(controller, "_ensure_horizontal_layout"):
            with self.assertRaisesRegex(WeReadError, "最后一页"):
                controller.next_page()

    def test_positioned_only_chapter_does_not_require_ocr_engine(self):
        controller, page = self.make_connected_controller()
        canvas_locator = Mock()
        canvas_locator.count.return_value = 0
        page.locator.return_value = canvas_locator
        page.evaluate.return_value = {
            "at_bottom": True,
            "position": 800,
            "maximum": 800,
            "chapter_height": 1600,
        }
        snapshots = [
            ["第一行。", "第二行。"],
            ["第二行。", "最后一行。"],
            ["第二行。", "最后一行。"],
        ]
        snapshot_calls = 0

        def positioned_rows(_canvas_bottom=None):
            nonlocal snapshot_calls
            value = snapshots[min(snapshot_calls, len(snapshots) - 1)]
            snapshot_calls += 1
            return value

        with (
            patch.object(
                controller,
                "_positioned_text_rows",
                side_effect=positioned_rows,
            ),
            patch.object(controller, "_get_ocr_engine") as get_ocr_engine,
        ):
            lines = controller._ocr_current_canvas()

        get_ocr_engine.assert_not_called()
        self.assertEqual(lines, ["第一行。", "第二行。", "最后一行。"])

    def test_ocr_restores_the_browser_scroll_position_after_scanning(self):
        controller, page = self.make_connected_controller()
        canvas = Mock()
        canvas.screenshot.return_value = png_bytes()
        locator = Mock()
        locator.count.return_value = 1
        locator.first = canvas
        page.locator.return_value = locator
        actions = []

        def evaluate(_script, action):
            actions.append(action)
            if action == "current":
                return {
                    "at_bottom": False,
                    "position": 320,
                    "maximum": 1000,
                    "chapter_height": 1800,
                }
            return {
                "at_bottom": True,
                "position": 1000,
                "maximum": 1000,
                "chapter_height": 1800,
            }

        page.evaluate.side_effect = evaluate
        controller._ocr_engine = Mock(
            return_value=(
                [
                    (
                        [[16, 170], [700, 170], [700, 216], [16, 216]],
                        "正文。",
                        0.98,
                    )
                ],
                None,
            )
        )

        with patch.object(controller, "_positioned_text_rows", return_value=[]):
            controller._ocr_current_canvas()

        self.assertEqual(actions[0], "current")
        self.assertEqual(actions[-1], {"position": 320})

    def test_ocr_ignores_canvas_header_navigation(self):
        controller, page = self.make_connected_controller()
        canvas = Mock()
        canvas.screenshot.return_value = png_bytes()
        locator = Mock()
        locator.count.return_value = 1
        locator.first = canvas
        page.locator.return_value = locator
        page.evaluate.return_value = {"at_bottom": True}
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
        canvas.screenshot.assert_any_call(type="png", scale="device")
        self.assertEqual(controller._ocr_engine.call_args.kwargs["unclip_ratio"], 1.8)
        self.assertEqual(
            [call.args[1] for call in page.evaluate.call_args_list[:3]],
            ["current", "top", "top"],
        )

    def test_ocr_merges_horizontal_fragments_into_one_visual_row(self):
        lines = [
            _OcrLine(100, 10, 140, 220, "同一行的前半，", 0.96),
            _OcrLine(102, 225, 141, 430, "同一行的后半。", 0.98),
            _OcrLine(180, 10, 220, 430, "下一行。", 0.97),
        ]

        merged = _merge_ocr_rows(lines)

        self.assertEqual(
            [line.text for line in merged],
            ["同一行的前半，同一行的后半。", "下一行。"],
        )

    def test_positioned_characters_are_rebuilt_by_visual_coordinates(self):
        characters = [
            {"x": 42, "y": 100, "text": "行"},
            {"x": 0, "y": 141, "text": "第"},
            {"x": 21, "y": 100, "text": "一"},
            {"x": 0, "y": 100, "text": "第"},
            {"x": 21, "y": 141, "text": "二"},
            {"x": 63, "y": 100, "text": "。\u200b"},
            {"x": 42, "y": 141, "text": "行。"},
        ]

        self.assertEqual(_positioned_rows(characters), ["第一行。", "第二行。"])

    def test_visual_boundaries_are_kept_while_dom_text_corrects_ocr(self):
        rows = ["第一行有错宇。", "第二行。", "尾声99"]
        paragraphs = ["第一行有错字。第二行。", "尾声。"]

        corrected = _reconcile_visual_rows(rows, paragraphs)

        self.assertEqual(corrected, ["第一行有错字。", "第二行。", "尾声。"])

    def test_partial_dom_text_does_not_replace_a_complete_visual_capture(self):
        rows = ["完整第一行。", "完整第二行。", "完整第三行。"]

        corrected = _reconcile_visual_rows(rows, ["只有一小段。"])

        self.assertEqual(corrected, rows)

    def test_ocr_appends_virtualized_positioned_rows_across_scroll_passes(self):
        controller, page = self.make_connected_controller()
        canvas = Mock()
        canvas.screenshot.return_value = png_bytes()
        locator = Mock()
        locator.count.return_value = 1
        locator.first = canvas
        page.locator.return_value = locator
        page.evaluate.return_value = {"at_bottom": True}
        controller._ocr_engine = Mock(
            return_value=(
                [
                    (
                        [[16, 170], [700, 170], [700, 216], [16, 216]],
                        "画布首行。",
                        0.98,
                    )
                ],
                None,
            )
        )
        snapshots = [
            [],
            ["中部第一行。", "中部第二行。"],
            ["中部第二行。", "尾部。"],
        ]
        snapshot_calls = 0

        def positioned_rows(_canvas_bottom=None):
            nonlocal snapshot_calls
            value = snapshots[min(snapshot_calls, len(snapshots) - 1)]
            snapshot_calls += 1
            return value

        with patch.object(
            controller,
            "_positioned_text_rows",
            side_effect=positioned_rows,
        ):
            lines = controller._ocr_current_canvas()

        self.assertEqual(
            lines,
            ["画布首行。", "中部第一行。", "中部第二行。", "尾部。"],
        )

    def test_ocr_reads_all_chapter_canvases_in_order(self):
        controller, page = self.make_connected_controller()
        first_canvas = Mock()
        first_canvas.screenshot.return_value = png_bytes()
        second_canvas = Mock()
        second_canvas.screenshot.return_value = png_bytes(height=401)
        locator = Mock()
        locator.count.return_value = 2
        locator.first = first_canvas
        locator.nth.return_value = second_canvas
        page.locator.return_value = locator
        page.evaluate.return_value = {"at_bottom": True}
        controller._ocr_engine = Mock(
            side_effect=[
                (
                    [
                        ([[0, 48], [180, 48], [180, 94], [0, 94]], "测试书", 0.99),
                        ([[16, 170], [700, 170], [700, 216], [16, 216]], "第一块正文。", 0.98),
                    ],
                    None,
                ),
                (
                    [
                        ([[16, 48], [700, 48], [700, 94], [16, 94]], "第二块顶部正文。", 0.97),
                    ],
                    None,
                ),
            ]
        )

        lines = controller._ocr_current_canvas()

        self.assertEqual(lines, ["第一块正文。", "第二块顶部正文。"])
        first_canvas.screenshot.assert_any_call(type="png", scale="device")
        second_canvas.screenshot.assert_any_call(type="png", scale="device")
        locator.nth.assert_any_call(1)

    def test_ocr_reads_canvas_pixels_without_element_screenshot_scrolling(self):
        controller, page = self.make_connected_controller()
        canvas = Mock()
        canvas.evaluate.return_value = png_data_url()
        canvas.screenshot.return_value = png_bytes()
        locator = Mock()
        locator.count.return_value = 1
        locator.first = canvas
        page.locator.return_value = locator
        page.evaluate.return_value = {"at_bottom": True}
        controller._ocr_engine = Mock(
            return_value=(
                [
                    (
                        [[16, 170], [700, 170], [700, 216], [16, 216]],
                        "正文不应触发页面跳动。",
                        0.98,
                    )
                ],
                None,
            )
        )

        lines = controller._ocr_current_canvas()

        self.assertEqual(lines, ["正文不应触发页面跳动。"])
        canvas.evaluate.assert_called()
        canvas.screenshot.assert_not_called()

    def test_ocr_pixel_changes_with_same_text_do_not_block_stable_completion(self):
        controller, page = self.make_connected_controller()
        canvas = Mock()
        screenshots = [png_data_url(height=400 + index) for index in range(4)]
        screenshot_calls = 0

        def changing_pixels(_script):
            nonlocal screenshot_calls
            value = screenshots[min(screenshot_calls, len(screenshots) - 1)]
            screenshot_calls += 1
            return value

        canvas.evaluate.side_effect = changing_pixels
        locator = Mock()
        locator.count.return_value = 1
        locator.first = canvas
        page.locator.return_value = locator
        page.evaluate.return_value = {"at_bottom": True}
        controller._ocr_engine = Mock(
            return_value=(
                [
                    (
                        [[16, 170], [700, 170], [700, 216], [16, 216]],
                        "相同正文。",
                        0.98,
                    )
                ],
                None,
            )
        )

        lines = controller._ocr_current_canvas()

        self.assertEqual(lines, ["相同正文。"])
        self.assertEqual(controller._ocr_engine.call_count, 4)

    def test_ocr_discovers_canvases_appended_while_scrolling(self):
        controller, page = self.make_connected_controller()
        canvases = [Mock(), Mock(), Mock()]
        for index, canvas in enumerate(canvases):
            canvas.screenshot.return_value = png_bytes(height=400 + index)

        locator = Mock()
        locator.count.side_effect = [1, 2, 3] + [3] * 9
        locator.first = canvases[0]
        locator.nth.side_effect = lambda index: canvases[index]
        page.locator.return_value = locator
        page.evaluate.return_value = {"at_bottom": True}
        controller._ocr_engine = Mock(
            side_effect=[
                (
                    [
                        (
                            [[16, 170], [700, 170], [700, 216], [16, 216]],
                            f"第{index + 1}块正文。",
                            0.98,
                        )
                    ],
                    None,
                )
                for index in range(3)
            ]
        )

        lines = controller._ocr_current_canvas()

        self.assertEqual(lines, ["第1块正文。", "第2块正文。", "第3块正文。"])
        self.assertEqual(controller._ocr_engine.call_count, 3)
        self.assertGreaterEqual(page.evaluate.call_count, 3)

    def test_ocr_discovers_a_canvas_redrawn_while_scrolling(self):
        controller, page = self.make_connected_controller()
        canvas = Mock()
        screenshots = [png_bytes(height=400 + index) for index in range(3)]
        screenshot_calls = 0

        def redrawn_canvas(**_kwargs):
            nonlocal screenshot_calls
            value = screenshots[min(screenshot_calls, len(screenshots) - 1)]
            screenshot_calls += 1
            return value

        canvas.screenshot.side_effect = redrawn_canvas
        locator = Mock()
        locator.count.return_value = 1
        locator.first = canvas
        page.locator.return_value = locator
        page.evaluate.side_effect = [
            {},
            {"at_bottom": False},
            {"at_bottom": False},
        ] + [{"at_bottom": True}] * 12
        controller._ocr_engine = Mock(
            side_effect=[
                (
                    [
                        (
                            [[16, 170], [700, 170], [700, 216], [16, 216]],
                            f"第{index + 1}屏正文。",
                            0.98,
                        )
                    ],
                    None,
                )
                for index in range(3)
            ]
        )

        with patch.object(controller, "_positioned_text_rows", return_value=[]):
            lines = controller._ocr_current_canvas()

        self.assertEqual(lines, ["第1屏正文。", "第2屏正文。", "第3屏正文。"])
        self.assertEqual(controller._ocr_engine.call_count, 3)

    def test_ocr_waits_through_temporary_bottom_for_delayed_canvas_growth(self):
        controller, page = self.make_connected_controller()
        first_canvas = Mock()
        first_canvas.evaluate.return_value = png_data_url(height=400)
        second_canvas = Mock()
        second_canvas.evaluate.return_value = png_data_url(height=401)
        locator = Mock()
        count_calls = 0

        def canvas_count():
            nonlocal count_calls
            count_calls += 1
            return 1 if count_calls <= 4 else 2

        locator.count.side_effect = canvas_count
        locator.first = first_canvas
        locator.nth.return_value = second_canvas
        page.locator.return_value = locator
        page.evaluate.return_value = {
            "at_bottom": True,
            "position": 800,
            "maximum": 800,
            "chapter_height": 1600,
        }
        controller._ocr_engine = Mock(
            side_effect=[
                (
                    [
                        (
                            [[16, 170], [700, 170], [700, 216], [16, 216]],
                            "前半章正文。",
                            0.98,
                        )
                    ],
                    None,
                ),
                (
                    [
                        (
                            [[16, 170], [700, 170], [700, 216], [16, 216]],
                            "后半章正文。",
                            0.98,
                        )
                    ],
                    None,
                ),
            ]
        )

        lines = controller._ocr_current_canvas()

        self.assertEqual(lines, ["前半章正文。", "后半章正文。"])
        self.assertGreater(count_calls, 4)

    def test_ocr_splits_tall_canvas_and_deduplicates_overlapping_lines(self):
        controller, page = self.make_connected_controller()
        canvas = Mock()
        canvas.screenshot.return_value = png_bytes(width=800, height=3000)
        locator = Mock()
        locator.count.return_value = 1
        locator.first = canvas
        page.locator.return_value = locator
        page.evaluate.return_value = {"at_bottom": True}
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
        page.evaluate.return_value = {"at_bottom": True}
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
        page.evaluate.return_value = {"at_bottom": True}
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
