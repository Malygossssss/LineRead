"""Playwright-backed WeRead source using only the rendered web page DOM."""

from __future__ import annotations

import hashlib
import os
import re
from base64 import b64decode
from binascii import Error as BinasciiError
from collections.abc import Iterator
from dataclasses import dataclass
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Mapping

from PIL import Image

from text_parser import DEFAULT_MAX_CHARS, ReaderSource, parse_text


WEREAD_HOME_URL = "https://weread.qq.com/"
WEREAD_READER_PATH = "/web/reader/"
CATALOG_CHAPTER_PREFIX = "catalog:"
OCR_CANVAS_HEADER_PX = 70
OCR_DEVICE_SCALE_FACTOR = 2
OCR_TILE_HEIGHT_PX = 1600
OCR_TILE_OVERLAP_PX = 160
OCR_DETECTION_UNCLIP_RATIO = 1.8
OCR_LOW_CONFIDENCE = 0.82
OCR_RETRY_SCALE = 2
OCR_RETRY_PADDING_PX = 12
OCR_CANVAS_DISCOVERY_WAIT_MS = 500
OCR_CANVAS_STABLE_PASSES = 8
OCR_CANVAS_MAX_DISCOVERY_PASSES = 200


class WeReadError(RuntimeError):
    """A user-facing WeRead connection or extraction failure."""


@dataclass(frozen=True)
class WeReadCatalogEntry:
    """One stable, display-ready entry in the current book catalog."""

    chapter_id: str
    title: str
    level: int = 1


@dataclass(frozen=True)
class WeReadChapter:
    """A display-ready snapshot of the current page in one chapter."""

    book_id: str
    book_title: str
    chapter_id: str
    chapter_title: str
    chapter_url: str
    units: tuple[str, ...]
    catalog: tuple[WeReadCatalogEntry, ...] = ()
    catalog_index: int = -1


@dataclass(frozen=True)
class _OcrLine:
    """One OCR result positioned in full-screenshot pixel coordinates."""

    top: float
    left: float
    bottom: float
    right: float
    text: str
    confidence: float


def default_profile_dir() -> Path:
    """Return a durable Chromium profile path outside the repository."""

    override = os.environ.get("LINEREAD_WEREAD_PROFILE", "").strip()
    if override:
        return Path(override).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    base = Path(local_app_data) if local_app_data else Path.home() / ".lineread"
    return base / "LineRead" / "WeReadProfile"


class WeReadController:
    """Own a persistent headed Chromium context for the WeRead website."""

    def __init__(
        self,
        profile_dir: str | Path | None = None,
        ocr_engine_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.profile_dir = Path(profile_dir) if profile_dir else default_profile_dir()
        self.browser_channel = ""
        self._ocr_engine_factory = ocr_engine_factory
        self._ocr_engine: Any = None
        self._playwright: Any = None
        self._context: Any = None
        self._page: Any = None

    @property
    def page(self) -> Any:
        if self._page is None or self._page.is_closed():
            raise WeReadError("尚未连接微信读书。")
        return self._page

    def connect(self) -> Any:
        """Launch or reuse the persistent browser and return its WeRead page."""

        if self._page is not None and not self._page.is_closed():
            return self._page

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise WeReadError(
                "项目虚拟环境尚未安装 Playwright，请先执行 "
                "`.venv\\Scripts\\python.exe -m pip install -r requirements.txt`。"
            ) from exc

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._playwright = sync_playwright().start()
            self._context = self._launch_persistent_context()
            self._page = self._select_browser_page()
            if "weread.qq.com" not in self._page.url:
                self._page.goto(WEREAD_HOME_URL, wait_until="domcontentloaded")
            return self._page
        except WeReadError:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise WeReadError(
                "无法启动微信读书浏览器。\n"
                f"底层错误：{_error_summary(exc)}"
            ) from exc

    def is_reader_page(self) -> bool:
        try:
            return WEREAD_READER_PATH in self.page.url
        except WeReadError:
            return False

    def readiness_state(self) -> str:
        """Return the visible startup state without reading private browser data."""

        self.connect()
        if self.is_reader_page():
            return "reader"
        try:
            state = self.page.evaluate(_EXTRACT_READINESS_STATE)
        except Exception:
            # Navigation can briefly destroy the JavaScript context. Treat the
            # transient state as book selection and check again on the next poll.
            return "book"
        return state if state in ("login", "book") else "book"

    def wait_for_reader(self, timeout_ms: int = 300_000) -> None:
        """Wait until the user has opened a book in the WeRead browser."""

        self.connect()
        try:
            self.page.wait_for_url(
                re.compile(r"https?://weread\.qq\.com/web/reader/"),
                timeout=timeout_ms,
            )
        except Exception as exc:
            raise WeReadError("等待微信读书打开书籍超时，请进入一本书后重试。") from exc

    def get_current_chapter(self) -> Mapping[str, Any]:
        """Extract only the currently visible horizontal reading page."""

        self.connect()
        if not self.is_reader_page():
            raise WeReadError("请先在微信读书浏览器中进入一本书。")
        try:
            self._wait_for_chapter_render()
            self._ensure_horizontal_layout()
            self._wait_for_chapter_render()
            catalog_position = self._catalog_position()
            payload = self.page.evaluate(_EXTRACT_CURRENT_CHAPTER)
        except WeReadError:
            # Keep actionable failures (for example, a missing layout control)
            # intact instead of hiding them behind a generic extraction error.
            raise
        except Exception as exc:
            raise WeReadError(
                "未能读取当前章节正文；微信读书页面结构可能已变化，请刷新后重试。\n"
                f"底层错误：{_error_summary(exc)}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise WeReadError("微信读书返回了无法识别的章节数据。")
        result = dict(payload)
        result["chapter_id"] = catalog_position["chapter_id"]
        result["catalog"] = catalog_position["entries"]
        result["catalog_index"] = catalog_position["index"]
        if catalog_position["title"]:
            result["chapter_title"] = catalog_position["title"]
        paragraphs = result.get("paragraphs")
        if (
            result.get("has_canvas") is True
            or result.get("uses_visual_renderer") is True
            or not isinstance(paragraphs, list)
            or not any(_text(item) for item in paragraphs)
        ):
            visual_rows = _strip_page_chrome(
                self._read_current_page_visual_rows(),
                _text(result.get("book_title")),
                _text(result.get("chapter_title")),
            )
            if visual_rows:
                result["paragraphs"] = _reconcile_visual_rows(
                    visual_rows,
                    paragraphs,
                )
        return result

    def next_page(self) -> None:
        """Turn to the next rendered page without preloading the chapter."""

        self._turn_page(1)

    def previous_page(self) -> None:
        """Turn to the previous rendered page without preloading the chapter."""

        self._turn_page(-1)

    def next_chapter(self) -> None:
        self._change_chapter(1)

    def previous_chapter(self) -> None:
        self._change_chapter(-1)

    def select_chapter(self, chapter_id: str) -> None:
        """Select one catalog chapter by its stable id."""

        if not isinstance(chapter_id, str) or not chapter_id:
            raise WeReadError("无法识别要打开的章节。")
        self._select_catalog_chapter(chapter_id)

    def restore_window(self) -> None:
        """Bring Chromium forward and best-effort restore a minimized window."""

        self.connect()
        try:
            self.page.bring_to_front()
            session = self._context.new_cdp_session(self.page)
            window = session.send("Browser.getWindowForTarget")
            session.send(
                "Browser.setWindowBounds",
                {
                    "windowId": window["windowId"],
                    "bounds": {"windowState": "normal"},
                },
            )
            session.detach()
        except Exception:
            # bring_to_front works on all supported engines; the CDP window-state
            # operation is Chromium/desktop-specific and intentionally best effort.
            try:
                self.page.bring_to_front()
            except Exception as exc:
                raise WeReadError("无法恢复微信读书窗口。") from exc

    def close(self) -> None:
        context, playwright = self._context, self._playwright
        self._page = None
        self._context = None
        self._playwright = None
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass

    def _select_browser_page(self) -> Any:
        """Reuse Chromium's startup page and remove surplus empty tabs."""

        pages = [
            page
            for page in list(getattr(self._context, "pages", []))
            if not page.is_closed()
        ]
        selected = next(
            (page for page in reversed(pages) if "weread.qq.com" in page.url),
            None,
        )
        if selected is None:
            selected = next(
                (page for page in reversed(pages) if _is_blank_page_url(page.url)),
                None,
            )
        if selected is None:
            selected = pages[-1] if pages else self._context.new_page()

        for page in pages:
            if page is selected or not _is_blank_page_url(page.url):
                continue
            try:
                page.close()
            except Exception:
                pass
        return selected

    def _launch_persistent_context(self) -> Any:
        """Launch bundled Chromium, with a narrow Windows Chrome fallback."""

        options = {
            "headless": False,
            "viewport": {"width": 1280, "height": 900},
            "device_scale_factor": OCR_DEVICE_SCALE_FACTOR,
        }
        chromium_error: Exception | None = None
        try:
            context = self._playwright.chromium.launch_persistent_context(
                str(self.profile_dir),
                **options,
            )
            self.browser_channel = "chromium"
            return context
        except Exception as exc:
            chromium_error = exc
            if not _should_fallback_to_chrome(exc):
                raise

        try:
            context = self._playwright.chromium.launch_persistent_context(
                str(self.profile_dir),
                channel="chrome",
                **options,
            )
            self.browser_channel = "chrome"
            return context
        except Exception as chrome_error:
            raise WeReadError(
                "Playwright Chromium 被 Windows 拒绝启动，回退到本机 Chrome 也失败。\n"
                f"Chromium：{_error_summary(chromium_error)}\n"
                f"Chrome：{_error_summary(chrome_error)}"
            ) from chrome_error

    def _ensure_horizontal_layout(self) -> None:
        """Use WeRead's own control to keep the reader in paginated mode."""

        horizontal = self.page.locator(".wr_horizontalReader")
        if horizontal.count() and horizontal.first.is_visible():
            return

        # WeRead names the switch by the *current* layout: vertical mode uses
        # ``isNormalReader`` while horizontal mode uses ``isHorizontalReader``.
        # The book-review control is absent in vertical mode, so locating the
        # switch relative to it fails precisely when a switch is needed.
        toggle = self.page.locator(".readerControls_item.isNormalReader")
        if toggle.count() == 0 or not toggle.first.is_visible():
            raise WeReadError("无法切换微信读书为翻页模式。")
        toggle.first.click()
        self.page.wait_for_function(
            "() => !!document.querySelector('.wr_horizontalReader')",
            timeout=10_000,
        )
        self.page.wait_for_timeout(300)

    def _turn_page(self, direction: int) -> None:
        """Click one public pager control and wait for a different render."""

        if direction not in (-1, 1):
            raise ValueError("翻页方向必须是 -1 或 1。")
        self._ensure_horizontal_layout()
        label = "下一页" if direction > 0 else "上一页"
        boundary = "最后一页" if direction > 0 else "第一页"
        selector = (
            ".renderTarget_pager_button.renderTarget_pager_button_right"
            if direction > 0
            else ".renderTarget_pager_button:not(.renderTarget_pager_button_right)"
        )
        button = self.page.locator(selector)
        if (
            button.count() == 0
            or not button.first.is_visible()
            or not button.first.is_enabled()
        ):
            raise WeReadError(f"当前已经是{boundary}。")

        before = self._page_signature()
        try:
            button.first.click()
            self.page.wait_for_function(
                _CURRENT_PAGE_CHANGED,
                before,
                polling=100,
                timeout=20_000,
            )
            self.page.wait_for_timeout(250)
            self._wait_for_chapter_render()
        except Exception as exc:
            raise WeReadError(f"微信读书{label}失败，请稍后重试。") from exc

    def _page_signature(self) -> str:
        value = self.page.evaluate(_EXTRACT_CURRENT_PAGE_SIGNATURE)
        return value if isinstance(value, str) else ""

    def _wait_for_chapter_render(self) -> None:
        """Wait past WeRead's small asynchronous loading shell."""

        self.page.wait_for_function(_CHAPTER_RENDER_READY, timeout=20_000)

    def _catalog_position(self) -> Mapping[str, Any]:
        payload = self.page.evaluate(_EXTRACT_CATALOG_POSITION)
        if not isinstance(payload, Mapping):
            raise WeReadError("无法读取微信读书目录。")
        entries = _catalog_entries(payload.get("items"))
        index = next(
            (position for position, entry in enumerate(entries) if entry["selected"]),
            -1,
        )
        if index < 0 or not entries:
            raise WeReadError("微信读书目录没有标记当前章节。")
        current = entries[index]
        return {
            "index": index,
            "count": len(entries),
            "chapter_id": current["chapter_id"],
            "title": current["title"],
            "entries": entries,
        }

    def _change_chapter(self, amount: int) -> None:
        position = self._catalog_position()
        target = position["index"] + amount
        if target < 0:
            raise WeReadError("当前已经是第一章。")
        if target >= position["count"]:
            raise WeReadError("当前已经是最后一章。")
        self._select_catalog_index(target)

    def _select_catalog_chapter(self, chapter_id: str) -> None:
        legacy_index = _catalog_index(chapter_id)
        if legacy_index is not None:
            self._select_catalog_index(legacy_index)
            return
        position = self._catalog_position()
        target = next(
            (
                index
                for index, entry in enumerate(position["entries"])
                if entry["chapter_id"] == chapter_id
            ),
            -1,
        )
        if target < 0:
            raise WeReadError("保存的章节已不在当前目录中。")
        self._select_catalog_index(target)

    def _select_catalog_index(self, index: int) -> None:
        items = self.page.locator(".readerCatalog_list_item")
        count = items.count()
        if index < 0 or index >= count:
            raise WeReadError("保存的章节已不在当前目录中。")

        target = items.nth(index)
        if target.evaluate(
            "node => node.classList.contains('readerCatalog_list_item_selected')"
        ):
            return
        if not target.is_visible():
            catalog_button = self.page.locator(".readerControls_item.catalog")
            if catalog_button.count() == 0 or not catalog_button.first.is_visible():
                raise WeReadError("无法打开微信读书目录。")
            catalog_button.first.click()
            self.page.wait_for_timeout(200)

        target.click()
        self.page.wait_for_function(
            """target => Array.from(
                document.querySelectorAll('.readerCatalog_list_item')
            ).findIndex(node => node.classList.contains('readerCatalog_list_item_selected')) === target""",
            arg=index,
            timeout=20_000,
        )
        self.page.wait_for_timeout(500)

    def _read_current_page_visual_rows(self) -> list[str]:
        """Read visible Canvas and positioned text without turning or scrolling."""

        rows = self._ocr_current_page_canvases()
        positioned = tuple(self._current_page_positioned_text_rows())
        if positioned:
            _extend_with_boundary_overlap(rows, positioned)
        return rows

    def _ocr_current_page_canvases(self) -> list[str]:
        """OCR only canvases mounted in the current horizontal render target."""

        canvas_locator = self.page.locator(".renderTargetContainer canvas")
        count = canvas_locator.count()
        if count == 0:
            return []

        engine = self._get_ocr_engine()
        texts: list[str] = []
        seen_canvas_hashes: set[bytes] = set()
        for canvas_index in range(count):
            canvas = (
                canvas_locator.first
                if canvas_index == 0
                else canvas_locator.nth(canvas_index)
            )
            if not canvas.is_visible():
                continue
            screenshot = _canvas_png(canvas)
            screenshot_hash = hashlib.sha256(screenshot).digest()
            if screenshot_hash in seen_canvas_hashes:
                continue
            seen_canvas_hashes.add(screenshot_hash)
            lines = _ocr_canvas_lines(engine, screenshot, ignore_header=False)
            _extend_with_boundary_overlap(
                texts,
                tuple(line.text for line in lines),
            )
        return texts

    def _current_page_positioned_text_rows(self) -> list[str]:
        """Rebuild only positioned characters mounted in the current page."""

        try:
            payload = self.page.evaluate(_EXTRACT_CURRENT_PAGE_POSITIONED_TEXT)
        except Exception:
            return []
        if not isinstance(payload, Mapping):
            return []
        return _positioned_rows(payload.get("characters"))

    def _ocr_current_canvas(self) -> list[str]:
        """Legacy full-chapter scanner retained for compatibility tests."""

        canvas_locator = self.page.locator(".readerChapterContent canvas")
        initial_scroll_state: Mapping[str, Any] | None = None
        try:
            scroll_state = self.page.evaluate(_MOVE_CANVAS_DISCOVERY, "current")
            if isinstance(scroll_state, Mapping):
                initial_scroll_state = scroll_state
            engine: Any = None
            texts: list[str] = []
            seen_canvas_hashes: set[bytes] = set()
            stable_bottom_passes = 0
            previous_canvas_count = 0
            previous_extent: tuple[int, int, int] | None = None
            canvas_coverage_bottom: float | None = None
            # WeRead can retain the virtualized last-screen nodes after the first
            # programmatic jump to the top. A second reset lets its scroll handler
            # discard that stale anchor; otherwise the next small move snaps all
            # the way back to the chapter bottom and skips the middle.
            for _reset_pass in range(2):
                self.page.evaluate(_MOVE_CANVAS_DISCOVERY, "top")
                self.page.wait_for_timeout(OCR_CANVAS_DISCOVERY_WAIT_MS)

            for discovery_pass in range(OCR_CANVAS_MAX_DISCOVERY_PASSES):
                canvas_count = canvas_locator.count()
                if canvas_count and engine is None:
                    engine = self._get_ocr_engine()

                discovered_canvas = False
                scan_start = (
                    max(0, previous_canvas_count - 1)
                    if canvas_count > previous_canvas_count
                    else 0
                )
                for canvas_index in range(scan_start, canvas_count):
                    canvas = (
                        canvas_locator.first
                        if canvas_index == 0
                        else canvas_locator.nth(canvas_index)
                    )
                    screenshot = _canvas_png(canvas)
                    screenshot_hash = hashlib.sha256(screenshot).digest()
                    if screenshot_hash in seen_canvas_hashes:
                        continue
                    first_canvas_image = not seen_canvas_hashes
                    seen_canvas_hashes.add(screenshot_hash)
                    lines = _ocr_canvas_lines(
                        engine,
                        screenshot,
                        ignore_header=first_canvas_image,
                    )
                    text_chunk = tuple(line.text for line in lines)
                    if not text_chunk:
                        continue
                    if _extend_with_boundary_overlap(texts, text_chunk):
                        discovered_canvas = True

                positioned_chunk = tuple(
                    self._positioned_text_rows(canvas_coverage_bottom)
                )
                if canvas_coverage_bottom is None:
                    canvas_coverage_bottom = getattr(
                        self,
                        "_last_positioned_canvas_bottom",
                        None,
                    )
                if positioned_chunk and _extend_with_boundary_overlap(
                    texts,
                    positioned_chunk,
                ):
                    discovered_canvas = True

                scroll_state = self.page.evaluate(_MOVE_CANVAS_DISCOVERY, "next")
                self.page.wait_for_timeout(OCR_CANVAS_DISCOVERY_WAIT_MS)
                at_bottom = bool(
                    isinstance(scroll_state, Mapping)
                    and scroll_state.get("at_bottom") is True
                )
                extent = (
                    canvas_count,
                    _mapping_int(scroll_state, "maximum"),
                    _mapping_int(scroll_state, "chapter_height"),
                )
                extent_changed = previous_extent is None or extent != previous_extent
                previous_extent = extent
                previous_canvas_count = canvas_count
                if discovered_canvas or extent_changed:
                    stable_bottom_passes = 0
                elif at_bottom:
                    stable_bottom_passes += 1
                else:
                    stable_bottom_passes = 0
                if stable_bottom_passes >= OCR_CANVAS_STABLE_PASSES:
                    break
            else:
                raise WeReadError(
                    "微信读书章节持续加载，未能确认正文结尾；请稍后重试。"
                )
        except WeReadError:
            raise
        except Exception as exc:
            raise WeReadError("本地 OCR 无法识别当前章节。") from exc
        finally:
            if initial_scroll_state is not None:
                position = initial_scroll_state.get("position")
                if isinstance(position, (int, float)) and not isinstance(position, bool):
                    try:
                        self.page.evaluate(
                            _MOVE_CANVAS_DISCOVERY,
                            {"position": position},
                        )
                    except Exception:
                        pass
        if not texts:
            raise WeReadError("本地 OCR 没有识别到当前章节正文。")
        return texts

    def _positioned_text_rows(
        self,
        canvas_coverage_bottom: float | None = None,
    ) -> list[str]:
        """Rebuild virtualized WeRead characters in visual row order."""

        try:
            payload = self.page.evaluate(
                _EXTRACT_POSITIONED_TEXT,
                canvas_coverage_bottom,
            )
        except Exception:
            return []
        if not isinstance(payload, Mapping):
            return []
        canvas_bottom = payload.get("canvas_bottom")
        if isinstance(canvas_bottom, (int, float)) and not isinstance(
            canvas_bottom,
            bool,
        ):
            self._last_positioned_canvas_bottom = float(canvas_bottom)
        return _positioned_rows(payload.get("characters"))

    def _get_ocr_engine(self) -> Any:
        if self._ocr_engine is not None:
            return self._ocr_engine
        try:
            if self._ocr_engine_factory is not None:
                self._ocr_engine = self._ocr_engine_factory()
            else:
                from rapidocr_onnxruntime import RapidOCR

                self._ocr_engine = RapidOCR()
        except ImportError as exc:
            raise WeReadError(
                "项目虚拟环境尚未安装中文 OCR，请重新执行 pip install -r requirements.txt。"
            ) from exc
        return self._ocr_engine


def _ocr_tiles(source: Image.Image) -> Iterator[tuple[int, Image.Image]]:
    """Yield overlapping vertical crops without changing browser scroll state."""

    if source.width <= 0 or source.height <= 0:
        return
    top = 0
    while top < source.height:
        bottom = min(top + OCR_TILE_HEIGHT_PX, source.height)
        yield top, source.crop((0, top, source.width, bottom))
        if bottom >= source.height:
            break
        top = bottom - OCR_TILE_OVERLAP_PX


def _canvas_png(canvas: Any) -> bytes:
    """Read Canvas pixels without Playwright scrolling the element into view."""

    try:
        data_url = canvas.evaluate(_EXTRACT_CANVAS_DATA_URL)
        if isinstance(data_url, str):
            header, separator, payload = data_url.partition(",")
            if (
                separator
                and header.lower().startswith("data:image/png;base64")
                and payload
            ):
                png = b64decode(payload, validate=True)
                if png:
                    return png
    except (BinasciiError, ValueError):
        pass
    except Exception:
        # A tainted or temporarily detached Canvas cannot be exported through
        # the DOM. Keep the old element screenshot as a narrow compatibility
        # fallback, although normal WeRead text canvases use the no-scroll path.
        pass
    return canvas.screenshot(type="png", scale="device")


def _extend_with_boundary_overlap(target: list[str], chunk: tuple[str, ...]) -> bool:
    """Append a newly rendered text chunk without repeating its leading overlap."""

    overlap = 0
    for size in range(min(len(target), len(chunk)), 0, -1):
        if tuple(target[-size:]) == chunk[:size]:
            overlap = size
            break
    additions = chunk[overlap:]
    target.extend(additions)
    return bool(additions)


def _mapping_int(value: Any, key: str) -> int:
    if not isinstance(value, Mapping):
        return -1
    item = value.get(key)
    if isinstance(item, (int, float)) and not isinstance(item, bool):
        return int(item)
    return -1


def _ocr_canvas_lines(
    engine: Any,
    screenshot: bytes,
    *,
    ignore_header: bool,
) -> list[_OcrLine]:
    """Recognize one canvas while preserving its local reading order."""

    lines: list[_OcrLine] = []
    with Image.open(BytesIO(screenshot)) as source:
        source.load()
        for tile_top, tile in _ocr_tiles(source):
            try:
                result = engine(
                    _image_png(tile),
                    unclip_ratio=OCR_DETECTION_UNCLIP_RATIO,
                )
                tile_lines = _ocr_lines(result, y_offset=tile_top)
                for line in tile_lines:
                    if (
                        ignore_header
                        and line.top
                        < OCR_CANVAS_HEADER_PX * OCR_DEVICE_SCALE_FACTOR
                    ):
                        continue
                    if line.confidence < OCR_LOW_CONFIDENCE:
                        line = _retry_ocr_line(engine, tile, tile_top, line)
                    lines.append(line)
            finally:
                tile.close()
    return _merge_ocr_rows(_deduplicate_ocr_lines(lines))


def _image_png(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _ocr_lines(result: Any, *, y_offset: int = 0) -> list[_OcrLine]:
    recognized = result[0] if isinstance(result, tuple) else result
    if not isinstance(recognized, list):
        return []

    lines: list[_OcrLine] = []
    for item in recognized:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        box, value = item[0], _text(item[1])
        if not value or not isinstance(box, (list, tuple)):
            continue
        try:
            points = [
                (float(point[0]), float(point[1]))
                for point in box
                if isinstance(point, (list, tuple)) and len(point) >= 2
            ]
        except (TypeError, ValueError):
            continue
        if not points:
            continue
        score = item[2] if len(item) >= 3 else 1.0
        confidence = float(score) if isinstance(score, (int, float)) else 1.0
        lines.append(
            _OcrLine(
                top=min(point[1] for point in points) + y_offset,
                left=min(point[0] for point in points),
                bottom=max(point[1] for point in points) + y_offset,
                right=max(point[0] for point in points),
                text=value,
                confidence=confidence,
            )
        )
    return lines


def _retry_ocr_line(
    engine: Any,
    tile: Image.Image,
    tile_top: int,
    original: _OcrLine,
) -> _OcrLine:
    """Retry one uncertain line and retain the original on any regression."""

    left = max(0, int(original.left) - OCR_RETRY_PADDING_PX)
    top = max(0, int(original.top - tile_top) - OCR_RETRY_PADDING_PX)
    right = min(tile.width, int(original.right + 1) + OCR_RETRY_PADDING_PX)
    bottom = min(
        tile.height,
        int(original.bottom - tile_top + 1) + OCR_RETRY_PADDING_PX,
    )
    if right <= left or bottom <= top:
        return original

    try:
        cropped = tile.crop((left, top, right, bottom))
        enlarged = cropped.resize(
            (cropped.width * OCR_RETRY_SCALE, cropped.height * OCR_RETRY_SCALE),
            Image.Resampling.LANCZOS,
        )
        retry_lines = _ocr_lines(
            engine(
                _image_png(enlarged),
                unclip_ratio=OCR_DETECTION_UNCLIP_RATIO,
            )
        )
    except Exception:
        return original
    finally:
        if "cropped" in locals():
            cropped.close()
        if "enlarged" in locals():
            enlarged.close()

    if len(retry_lines) != 1:
        return original
    candidate = retry_lines[0]
    if candidate.confidence <= original.confidence:
        return original
    return _OcrLine(
        top=original.top,
        left=original.left,
        bottom=original.bottom,
        right=original.right,
        text=candidate.text,
        confidence=candidate.confidence,
    )


def _deduplicate_ocr_lines(lines: list[_OcrLine]) -> list[_OcrLine]:
    unique: list[_OcrLine] = []
    for line in sorted(lines, key=lambda item: (item.top, item.left)):
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(unique)
                if _same_detected_line(existing, line)
            ),
            None,
        )
        if duplicate_index is None:
            unique.append(line)
        elif line.confidence > unique[duplicate_index].confidence:
            unique[duplicate_index] = line
    return sorted(unique, key=lambda item: (item.top, item.left))


def _merge_ocr_rows(lines: list[_OcrLine]) -> list[_OcrLine]:
    """Merge horizontally separated OCR boxes that share one visual row."""

    groups: list[list[_OcrLine]] = []
    for line in sorted(lines, key=lambda item: (item.top, item.left)):
        matching_group: list[_OcrLine] | None = None
        for group in reversed(groups):
            group_top = min(item.top for item in group)
            group_bottom = max(item.bottom for item in group)
            overlap = min(group_bottom, line.bottom) - max(group_top, line.top)
            line_height = max(1.0, line.bottom - line.top)
            group_height = max(1.0, group_bottom - group_top)
            center_distance = abs(
                (line.top + line.bottom) / 2
                - (group_top + group_bottom) / 2
            )
            if (
                overlap >= min(line_height, group_height) * 0.45
                or center_distance <= min(line_height, group_height) * 0.35
            ):
                matching_group = group
                break
            if line.top > group_bottom + max(line_height, group_height):
                break
        if matching_group is None:
            groups.append([line])
        else:
            matching_group.append(line)

    merged: list[_OcrLine] = []
    for group in groups:
        fragments = sorted(group, key=lambda item: item.left)
        text = fragments[0].text
        previous = fragments[0]
        for fragment in fragments[1:]:
            text += _ocr_fragment_separator(previous, fragment) + fragment.text
            previous = fragment
        merged.append(
            _OcrLine(
                top=min(item.top for item in fragments),
                left=min(item.left for item in fragments),
                bottom=max(item.bottom for item in fragments),
                right=max(item.right for item in fragments),
                text=text,
                confidence=min(item.confidence for item in fragments),
            )
        )
    return sorted(merged, key=lambda item: (item.top, item.left))


def _ocr_fragment_separator(left: _OcrLine, right: _OcrLine) -> str:
    if not left.text or not right.text:
        return ""
    if not (
        left.text[-1].isascii()
        and left.text[-1].isalnum()
        and right.text[0].isascii()
        and right.text[0].isalnum()
    ):
        return ""
    gap = right.left - left.right
    average_height = (
        max(1.0, left.bottom - left.top) + max(1.0, right.bottom - right.top)
    ) / 2
    return " " if gap > average_height * 0.12 else ""


def _positioned_rows(value: Any) -> list[str]:
    """Group positioned characters by visible page and y, then sort by x."""

    if not isinstance(value, list):
        return []
    rows: dict[tuple[int, int], dict[float, str]] = {}
    for item in value:
        if not isinstance(item, Mapping):
            continue
        x, y, text = item.get("x"), item.get("y"), item.get("text")
        if (
            not isinstance(x, (int, float))
            or isinstance(x, bool)
            or not isinstance(y, (int, float))
            or isinstance(y, bool)
            or not isinstance(text, str)
        ):
            continue
        cleaned = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
        if not cleaned:
            continue
        raw_page = item.get("page", 0)
        page_number = (
            int(raw_page)
            if isinstance(raw_page, (int, float)) and not isinstance(raw_page, bool)
            else 0
        )
        row = rows.setdefault((page_number, round(float(y))), {})
        row.setdefault(round(float(x), 2), cleaned)

    result: list[str] = []
    for row_key in sorted(rows):
        text = "".join(rows[row_key][x] for x in sorted(rows[row_key])).strip()
        if text:
            result.append(text)
    return result


def _strip_page_chrome(
    rows: list[str],
    book_title: str,
    chapter_title: str,
) -> list[str]:
    """Remove exact reader chrome labels while retaining real body lines."""

    excluded = {
        _clean_render_text(value)
        for value in (book_title, chapter_title, "上一页", "下一页")
        if _clean_render_text(value)
    }
    return [
        cleaned
        for row in rows
        if (cleaned := _clean_render_text(row)) and cleaned not in excluded
    ]


def _reconcile_visual_rows(
    visual_rows: list[str],
    semantic_paragraphs: Any,
) -> list[str]:
    """Keep measured row boundaries while correcting OCR from rendered DOM text."""

    rows = [_clean_render_text(row) for row in visual_rows]
    rows = [row for row in rows if row]
    if not rows or not isinstance(semantic_paragraphs, (list, tuple)):
        return rows

    exact = "".join(
        _clean_render_text(paragraph)
        for paragraph in semantic_paragraphs
        if isinstance(paragraph, str)
    )
    recognized = "".join(rows)
    if not exact or not recognized:
        return rows
    length_ratio = len(exact) / len(recognized)
    if not 0.8 <= length_ratio <= 1.2:
        return rows

    matcher = SequenceMatcher(None, recognized, exact, autojunk=False)
    if matcher.ratio() < 0.8:
        return rows

    boundary_map = [0] * (len(recognized) + 1)
    for _tag, source_start, source_end, exact_start, exact_end in matcher.get_opcodes():
        source_span = source_end - source_start
        exact_span = exact_end - exact_start
        if source_span == 0:
            boundary_map[source_start] = exact_end
            continue
        for offset in range(source_span + 1):
            boundary_map[source_start + offset] = exact_start + round(
                exact_span * offset / source_span
            )

    boundary_map[0] = 0
    boundary_map[-1] = len(exact)
    for index in range(1, len(boundary_map)):
        boundary_map[index] = max(boundary_map[index - 1], boundary_map[index])

    corrected: list[str] = []
    source_end = 0
    exact_start = 0
    for row_index, row in enumerate(rows):
        source_end += len(row)
        exact_end = (
            len(exact)
            if row_index == len(rows) - 1
            else boundary_map[source_end]
        )
        text = exact[exact_start:exact_end].strip()
        if text:
            corrected.append(text)
        exact_start = exact_end
    return corrected or rows


def _clean_render_text(value: str) -> str:
    without_markers = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", value)
    return " ".join(without_markers.replace("\r", "\n").split()).strip()


def _same_detected_line(first: _OcrLine, second: _OcrLine) -> bool:
    vertical_overlap = min(first.bottom, second.bottom) - max(first.top, second.top)
    horizontal_overlap = min(first.right, second.right) - max(first.left, second.left)
    min_height = min(first.bottom - first.top, second.bottom - second.top)
    min_width = min(first.right - first.left, second.right - second.left)
    return (
        min_height > 0
        and min_width > 0
        and vertical_overlap >= min_height * 0.5
        and horizontal_overlap >= min_width * 0.5
    )


class WeReadSource(ReaderSource):
    """Convert the current rendered page into cached single-line units."""

    def __init__(
        self,
        controller: WeReadController,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> None:
        self.controller = controller
        self.max_chars = max_chars
        self.cached_chapter: WeReadChapter | None = None

    def connect(self) -> None:
        self.controller.connect()

    def get_units(self) -> list[str]:
        return list(self.load_current_chapter().units)

    def load_current_chapter(self) -> WeReadChapter:
        chapter = self._chapter_from_payload(self.controller.get_current_chapter())
        self.cached_chapter = chapter
        return chapter

    def next_chapter(self) -> WeReadChapter:
        self.controller.next_chapter()
        return self.load_current_chapter()

    def previous_chapter(self) -> WeReadChapter:
        self.controller.previous_chapter()
        return self.load_current_chapter()

    def next_page(self) -> WeReadChapter:
        self.controller.next_page()
        return self.load_current_chapter()

    def previous_page(self) -> WeReadChapter:
        self.controller.previous_page()
        return self.load_current_chapter()

    def select_chapter(self, chapter_id: str) -> WeReadChapter:
        self.controller.select_chapter(chapter_id)
        return self.load_current_chapter()

    def restore_window(self) -> None:
        self.controller.restore_window()

    def switch_book(self) -> WeReadChapter:
        self.restore_window()
        return self.load_current_chapter()

    def close(self) -> None:
        self.controller.close()

    def _chapter_from_payload(self, payload: Mapping[str, Any]) -> WeReadChapter:
        book_title = _text(payload.get("book_title"))
        chapter_title = _text(payload.get("chapter_title")) or "当前章节"
        chapter_url = _text(payload.get("chapter_url"))
        if not book_title:
            raise WeReadError("未能识别当前书名，请确认已进入微信读书阅读页。")

        paragraphs = payload.get("paragraphs")
        if not isinstance(paragraphs, (list, tuple)):
            raise WeReadError("当前章节正文格式无法识别。")
        units: list[str] = []
        for paragraph in paragraphs:
            if isinstance(paragraph, str):
                units.extend(parse_text(paragraph, self.max_chars))
        if not units:
            raise WeReadError("当前章节没有可阅读的正文。")

        book_id = _text(payload.get("book_id")) or _stable_id("book", book_title)
        chapter_id = _text(payload.get("chapter_id")) or _stable_id(
            "chapter", f"{chapter_url}\n{chapter_title}"
        )
        catalog = _chapter_catalog(payload.get("catalog"))
        catalog_index = payload.get("catalog_index", -1)
        if (
            not isinstance(catalog_index, int)
            or isinstance(catalog_index, bool)
            or catalog_index < 0
            or catalog_index >= len(catalog)
        ):
            catalog_index = next(
                (
                    index
                    for index, entry in enumerate(catalog)
                    if entry.chapter_id == chapter_id
                ),
                -1,
            )
        return WeReadChapter(
            book_id=book_id,
            book_title=book_title,
            chapter_id=chapter_id,
            chapter_title=chapter_title,
            chapter_url=chapter_url,
            units=tuple(units),
            catalog=catalog,
            catalog_index=catalog_index,
        )


def _text(value: Any) -> str:
    return " ".join(value.replace("\r", "\n").split()).strip() if isinstance(value, str) else ""


def _chapter_catalog(value: Any) -> tuple[WeReadCatalogEntry, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    entries: list[WeReadCatalogEntry] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        chapter_id = _text(item.get("chapter_id"))
        title = _text(item.get("title"))
        level = item.get("level", 1)
        if not chapter_id or not title:
            continue
        if not isinstance(level, int) or isinstance(level, bool) or level < 1:
            level = 1
        entries.append(WeReadCatalogEntry(chapter_id, title, level))
    return tuple(entries)


def _error_summary(error: BaseException) -> str:
    text = str(error).strip()
    return text.splitlines()[0] if text else type(error).__name__


def _should_fallback_to_chrome(error: BaseException) -> bool:
    if os.name != "nt":
        return False
    message = str(error).casefold()
    return "spawn unknown" in message or "executable doesn't exist" in message


def _is_blank_page_url(url: Any) -> bool:
    return isinstance(url, str) and url.casefold() in {
        "about:blank",
        "chrome://newtab/",
        "chrome://new-tab-page/",
    }


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _catalog_index(chapter_id: str) -> int | None:
    if not isinstance(chapter_id, str) or not chapter_id.startswith(CATALOG_CHAPTER_PREFIX):
        return None
    value = chapter_id[len(CATALOG_CHAPTER_PREFIX) :]
    return int(value) if value.isdigit() else None


def _catalog_entries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    entries: list[dict[str, Any]] = []
    parent_title = ""
    occurrences: dict[tuple[str, str], int] = {}
    for item in value:
        if not isinstance(item, Mapping):
            continue
        title = _text(item.get("title"))
        level = item.get("level")
        if not title or not isinstance(level, int):
            continue
        if level <= 1:
            parent = ""
            parent_title = title
        else:
            parent = parent_title
        occurrence_key = (parent, title)
        occurrence = occurrences.get(occurrence_key, 0)
        occurrences[occurrence_key] = occurrence + 1
        material = f"{parent}\n{title}\n{occurrence}"
        entries.append(
            {
                "chapter_id": f"{CATALOG_CHAPTER_PREFIX}{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}",
                "title": title,
                "level": level,
                "selected": item.get("selected") is True,
            }
        )
    return entries


_EXTRACT_CATALOG_POSITION = r"""
() => {
  const items = Array.from(document.querySelectorAll('.readerCatalog_list_item'));
  return {
    items: items.map(node => {
      const inner = node.querySelector('.readerCatalog_list_item_inner');
      const titleNode = node.querySelector(
        '.readerCatalog_list_item_title_text, .readerCatalog_list_item_title'
      );
      const levelMatch = inner && inner.className.match(/readerCatalog_list_item_level_(\d+)/);
      return {
        title: titleNode ? (titleNode.innerText || titleNode.textContent || '').trim() : '',
        level: levelMatch ? Number(levelMatch[1]) : 1,
        selected: node.classList.contains('readerCatalog_list_item_selected')
      };
    })
  };
}
"""


_EXTRACT_READINESS_STATE = r"""
() => {
  const visible = (node) => {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' &&
      rect.width > 0 && rect.height > 0;
  };
  const loginSelectors = [
    '.navBar_login', '.loginDialog', '[class*="loginDialog"]',
    '[class*="LoginDialog"]', '[class*="login_dialog"]'
  ];
  if (loginSelectors.some(selector =>
    Array.from(document.querySelectorAll(selector)).some(visible)
  )) return 'login';

  const loginLabels = new Set(['登录', '扫码登录', '微信登录', '登录微信读书']);
  const loginControl = Array.from(
    document.querySelectorAll('button, a, [role="button"]')
  ).find(node => visible(node) && loginLabels.has(
    (node.innerText || node.textContent || '').replace(/\s+/g, '').trim()
  ));
  return loginControl ? 'login' : 'book';
}
"""


_CHAPTER_RENDER_READY = r"""
() => {
  const chapter = document.querySelector('.readerChapterContent');
  if (!chapter) return false;
  if (chapter.scrollHeight <= 300) return false;
  const visible = (node) => {
    const style = window.getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' &&
      rect.width > 0 && rect.height > 0;
  };
  const paragraphReady = Array.from(chapter.querySelectorAll('p')).some(node =>
    visible(node) && (node.innerText || node.textContent || '').trim().length >= 2
  );
  const positionedCount = Array.from(
    chapter.querySelectorAll('[data-wr-role="text"]')
  ).filter(visible).length;
  const canvasReady = Array.from(chapter.querySelectorAll('canvas')).some(canvas => {
    const rect = canvas.getBoundingClientRect();
    return canvas.width > 0 && canvas.height > 0 &&
      rect.width > 100 && rect.height > 100;
  });
  return paragraphReady || positionedCount >= 2 || canvasReady;
}
"""


_EXTRACT_CURRENT_PAGE_SIGNATURE = r"""
() => {
  const root = document.querySelector('.renderTargetContainer') ||
    document.querySelector('.readerChapterContent');
  if (!root) return '';
  const visible = (node) => {
    const style = window.getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' &&
      rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0 &&
      rect.top < window.innerHeight && rect.left < window.innerWidth;
  };
  const hash = (value) => {
    let result = 2166136261;
    const step = Math.max(1, Math.floor(value.length / 4096));
    for (let index = 0; index < value.length; index += step) {
      result ^= value.charCodeAt(index);
      result = Math.imul(result, 16777619);
    }
    return (result >>> 0).toString(16);
  };
  const selected = Array.from(
    document.querySelectorAll('.readerCatalog_list_item')
  ).findIndex(node => node.classList.contains('readerCatalog_list_item_selected'));
  const text = Array.from(root.querySelectorAll(
    'p, h1, h2, h3, [data-wr-role="text"]'
  )).filter(visible).map(node => {
    const rect = node.getBoundingClientRect();
    return `${Math.round(rect.left)},${Math.round(rect.top)}:${node.textContent || ''}`;
  }).join('|');
  const canvases = Array.from(root.querySelectorAll('canvas')).filter(visible).map(
    canvas => {
      try {
        const pixels = canvas.toDataURL('image/png');
        return `${canvas.width}x${canvas.height}:${pixels.length}:${hash(pixels)}`;
      } catch (error) {
        const rect = canvas.getBoundingClientRect();
        return `${canvas.width}x${canvas.height}:${Math.round(rect.left)},${Math.round(rect.top)}`;
      }
    }
  ).join('|');
  return `${selected}:${hash(text)}:${canvases}`;
}
"""


_CURRENT_PAGE_CHANGED = r"""
(previous) => {
  const root = document.querySelector('.renderTargetContainer') ||
    document.querySelector('.readerChapterContent');
  if (!root) return false;
  const visible = (node) => {
    const style = window.getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' &&
      rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0 &&
      rect.top < window.innerHeight && rect.left < window.innerWidth;
  };
  const hash = (value) => {
    let result = 2166136261;
    const step = Math.max(1, Math.floor(value.length / 4096));
    for (let index = 0; index < value.length; index += step) {
      result ^= value.charCodeAt(index);
      result = Math.imul(result, 16777619);
    }
    return (result >>> 0).toString(16);
  };
  const selected = Array.from(
    document.querySelectorAll('.readerCatalog_list_item')
  ).findIndex(node => node.classList.contains('readerCatalog_list_item_selected'));
  const text = Array.from(root.querySelectorAll(
    'p, h1, h2, h3, [data-wr-role="text"]'
  )).filter(visible).map(node => {
    const rect = node.getBoundingClientRect();
    return `${Math.round(rect.left)},${Math.round(rect.top)}:${node.textContent || ''}`;
  }).join('|');
  const canvases = Array.from(root.querySelectorAll('canvas')).filter(visible).map(
    canvas => {
      try {
        const pixels = canvas.toDataURL('image/png');
        return `${canvas.width}x${canvas.height}:${pixels.length}:${hash(pixels)}`;
      } catch (error) {
        const rect = canvas.getBoundingClientRect();
        return `${canvas.width}x${canvas.height}:${Math.round(rect.left)},${Math.round(rect.top)}`;
      }
    }
  ).join('|');
  const current = `${selected}:${hash(text)}:${canvases}`;
  return !!current && current !== previous;
}
"""


_EXTRACT_CANVAS_DATA_URL = r"""
(canvas) => {
  if (!(canvas instanceof HTMLCanvasElement) || !canvas.width || !canvas.height) {
    return '';
  }
  try {
    return canvas.toDataURL('image/png');
  } catch (error) {
    return '';
  }
}
"""


_EXTRACT_CURRENT_PAGE_POSITIONED_TEXT = r"""
() => {
  const root = document.querySelector('.renderTargetContainer');
  if (!root) return {characters: []};
  const rootRect = root.getBoundingClientRect();
  const visible = (node) => {
    const style = window.getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' &&
      rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0 &&
      rect.top < window.innerHeight && rect.left < window.innerWidth;
  };
  const visiblePages = Array.from(root.querySelectorAll('.page_show'))
    .filter(visible)
    .sort((first, second) =>
      first.getBoundingClientRect().left - second.getBoundingClientRect().left
    );
  const visibleCanvases = Array.from(root.querySelectorAll('canvas')).filter(visible);
  const twoPage = visiblePages.length > 1 || visibleCanvases.length > 1;
  const midpoint = rootRect.left + rootRect.width / 2;
  const characters = [];
  for (const node of root.querySelectorAll('[data-wr-role="text"]')) {
    if (!visible(node)) continue;
    const text = node.textContent || '';
    if (!text) continue;
    const rect = node.getBoundingClientRect();
    const pageNode = node.closest('.page_left, .page_right');
    let page = pageNode ? visiblePages.indexOf(pageNode) : -1;
    if (page < 0) page = twoPage && rect.left >= midpoint ? 1 : 0;
    const pageRect = pageNode && visible(pageNode)
      ? pageNode.getBoundingClientRect()
      : rootRect;
    characters.push({
      page,
      x: rect.left - pageRect.left,
      y: rect.top - pageRect.top,
      text
    });
  }
  return {characters};
}
"""


_EXTRACT_POSITIONED_TEXT = r"""
(fixedCanvasBottom) => {
  const chapter = document.querySelector('.readerChapterContent');
  if (!chapter) return {characters: []};
  const chapterRect = chapter.getBoundingClientRect();
  const measuredCanvasBottom = Array.from(chapter.querySelectorAll('canvas')).reduce(
    (bottom, canvas) => Math.max(
      bottom,
      canvas.getBoundingClientRect().bottom - chapterRect.top
    ),
    Number.NEGATIVE_INFINITY
  );
  const canvasBottom = Number.isFinite(fixedCanvasBottom)
    ? fixedCanvasBottom
    : measuredCanvasBottom;
  const characters = [];
  for (const node of chapter.querySelectorAll('[data-wr-role="text"]')) {
    const style = window.getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    if (style.display === 'none' || style.visibility === 'hidden' ||
        rect.width <= 0 || rect.height <= 0) continue;
    const text = node.textContent || '';
    if (!text) continue;
    const y = rect.top - chapterRect.top;
    if (Number.isFinite(canvasBottom) && y < canvasBottom - 2) continue;
    characters.push({
      x: rect.left - chapterRect.left,
      y,
      text
    });
  }
  return {
    characters,
    canvas_bottom: Number.isFinite(canvasBottom) ? canvasBottom : null,
    measured_canvas_bottom: Number.isFinite(measuredCanvasBottom)
      ? measuredCanvasBottom
      : null
  };
}
"""


_MOVE_CANVAS_DISCOVERY = r"""
(action) => {
  const chapter = document.querySelector('.readerChapterContent');
  if (!chapter) return {at_bottom: false, moved: false};

  const isScrollable = (node) => {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    return /(auto|scroll|overlay)/.test(style.overflowY) &&
      node.scrollHeight > node.clientHeight + 2;
  };

  let scroller = chapter;
  while (scroller && scroller !== document.documentElement &&
         !isScrollable(scroller)) {
    scroller = scroller.parentElement;
  }
  if (!isScrollable(scroller)) {
    scroller = document.scrollingElement || document.documentElement;
  }

  const documentScroller = scroller === document.body ||
    scroller === document.documentElement ||
    scroller === document.scrollingElement;
  const viewportHeight = documentScroller ? window.innerHeight : scroller.clientHeight;
  const before = documentScroller ? window.scrollY : scroller.scrollTop;
  const maximum = Math.max(0, scroller.scrollHeight - viewportHeight);
  const requestedPosition = action && typeof action === 'object'
    ? Number(action.position)
    : NaN;
  const target = action === 'top' ? 0 : (
    action === 'current' ? before : (
      Number.isFinite(requestedPosition)
        ? Math.max(0, Math.min(maximum, requestedPosition))
        : Math.min(
            maximum,
            before + Math.max(200, Math.floor(viewportHeight * 0.8))
          )
    )
  );
  if (documentScroller) {
    window.scrollTo(0, target);
  } else {
    scroller.scrollTop = target;
  }
  const after = documentScroller ? window.scrollY : scroller.scrollTop;
  if (action === 'next' && after >= maximum - 2 && after <= before + 1) {
    scroller.dispatchEvent(new Event('scroll', {bubbles: true}));
    if (!documentScroller) {
      window.dispatchEvent(new Event('scroll'));
    }
  }
  return {
    at_bottom: after >= maximum - 2,
    moved: after > before + 1,
    position: after,
    maximum,
    chapter_height: chapter.scrollHeight
  };
}
"""


_EXTRACT_CURRENT_CHAPTER = r"""
() => {
  const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const visible = (node) => {
    const style = window.getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' &&
      rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0 &&
      rect.top < window.innerHeight && rect.left < window.innerWidth;
  };
  const chapter = document.querySelector('.readerChapterContent');
  if (!chapter) return null;
  const renderRoot = chapter.querySelector('.renderTargetContainer') || chapter;

  const paragraphNodes = Array.from(renderRoot.querySelectorAll('p')).filter(visible);
  const paragraphs = [];
  for (const node of paragraphNodes) {
    const text = clean(node.innerText || node.textContent);
    if (text && paragraphs[paragraphs.length - 1] !== text) paragraphs.push(text);
  }

  const firstText = (selectors, root = document) => {
    for (const selector of selectors) {
      const node = root.querySelector(selector);
      const text = node && clean(node.innerText || node.textContent);
      if (text) return text;
    }
    return '';
  };
  const attr = (node, names) => {
    for (const name of names) {
      const value = node.getAttribute(name);
      if (value) return value;
    }
    return '';
  };

  const bookLink = document.querySelector('a[href*="/web/bookDetail/"]');
  const bookMatch = bookLink && bookLink.href.match(/\/web\/bookDetail\/([^/?#]+)/);
  const readerMatch = window.location.pathname.match(/\/web\/reader\/([^/?#]+)/);
  const catalogItems = Array.from(document.querySelectorAll('.readerCatalog_list_item'));
  const catalogIndex = catalogItems.findIndex(
    node => node.classList.contains('readerCatalog_list_item_selected')
  );
  const selectedCatalog = catalogIndex >= 0 ? catalogItems[catalogIndex] : null;
  const selectedTitle = selectedCatalog && firstText([
    '.readerCatalog_list_item_title_text', '.readerCatalog_list_item_title'
  ], selectedCatalog);
  const rawTitle = clean(document.title).replace(/\s*[-|_]\s*微信读书\s*$/, '');
  return {
    book_id: bookMatch ? bookMatch[1] : (
      readerMatch ? readerMatch[1] : attr(document.body, ['data-book-id', 'data-bookid'])
    ),
    book_title: firstText([
      '[class*="readerTopBar_title"]', '[class*="bookInfo_title"]',
      '[class*="readerBookInfo"] [class*="title"]'
    ]) || rawTitle,
    chapter_id: catalogIndex >= 0 ? `catalog:${catalogIndex}` : attr(
      chapter, ['data-chapter-id', 'data-chapter-uid', 'data-chapteruid', 'id']
    ),
    chapter_title: selectedTitle || firstText([
      '.renderTargetPageInfo_header_chapterTitle', 'h1', 'h2', 'h3',
      '[class*="chapterTitle"]', '[class*="chapter_title"]'
    ], renderRoot) || firstText(['[class*="readerTopBar_chapter"]']),
    chapter_url: window.location.href,
    has_canvas: Array.from(renderRoot.querySelectorAll('canvas')).some(visible),
    uses_visual_renderer: Array.from(
      renderRoot.querySelectorAll('[data-wr-role="text"]')
    ).some(visible),
    paragraphs
  };
}
"""
