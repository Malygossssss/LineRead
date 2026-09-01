"""Playwright-backed WeRead source using only the rendered web page DOM."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
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


class WeReadError(RuntimeError):
    """A user-facing WeRead connection or extraction failure."""


@dataclass(frozen=True)
class WeReadChapter:
    """A fully cached, display-ready chapter snapshot."""

    book_id: str
    book_title: str
    chapter_id: str
    chapter_title: str
    chapter_url: str
    units: tuple[str, ...]


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
        """Extract the currently rendered chapter from public page DOM nodes."""

        self.connect()
        if not self.is_reader_page():
            raise WeReadError("请先在微信读书浏览器中进入一本书。")
        try:
            self._ensure_vertical_layout()
            self.page.wait_for_function(
                """() => {
                    const chapter = document.querySelector('.readerChapterContent');
                    return !!chapter && !!chapter.querySelector('canvas, p');
                }""",
                timeout=20_000,
            )
            catalog_position = self._catalog_position()
            payload = self.page.evaluate(_EXTRACT_CURRENT_CHAPTER)
        except Exception as exc:
            raise WeReadError(
                "未能读取当前章节正文；微信读书页面结构可能已变化，请刷新后重试。"
            ) from exc
        if not isinstance(payload, Mapping):
            raise WeReadError("微信读书返回了无法识别的章节数据。")
        result = dict(payload)
        result["chapter_id"] = catalog_position["chapter_id"]
        if catalog_position["title"]:
            result["chapter_title"] = catalog_position["title"]
        paragraphs = result.get("paragraphs")
        if not isinstance(paragraphs, list) or not any(_text(item) for item in paragraphs):
            result["paragraphs"] = self._ocr_current_canvas()
        return result

    def next_chapter(self) -> None:
        self._change_chapter(1)

    def previous_chapter(self) -> None:
        self._change_chapter(-1)

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

    def open_chapter_url(self, url: str, chapter_id: str = "") -> None:
        """Open a previously captured WeRead chapter URL in the same browser."""

        if not _is_safe_weread_reader_url(url):
            raise WeReadError("保存的微信读书章节地址无效。")
        try:
            self.connect().goto(url, wait_until="domcontentloaded")
            if chapter_id.startswith(CATALOG_CHAPTER_PREFIX):
                self.page.wait_for_function(
                    "() => document.querySelectorAll('.readerCatalog_list_item').length > 0",
                    timeout=20_000,
                )
                self._select_catalog_chapter(chapter_id)
        except Exception as exc:
            raise WeReadError("无法恢复保存的微信读书章节。") from exc

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

    def _ensure_vertical_layout(self) -> None:
        """Use WeRead's own layout control when horizontal mode is active."""

        toggle = self.page.locator(".readerControls_item.isHorizontalReader")
        if toggle.count() == 0 or not toggle.first.is_visible():
            return
        toggle.first.click()
        self.page.wait_for_function(
            "() => !document.querySelector('.wr_horizontalReader')",
            timeout=10_000,
        )
        self.page.wait_for_timeout(300)

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

    def _ocr_current_canvas(self) -> list[str]:
        canvas_locator = self.page.locator(".readerChapterContent canvas")
        if canvas_locator.count() == 0:
            raise WeReadError("当前章节既没有正文 DOM，也没有可识别的 Canvas。")
        try:
            screenshot = canvas_locator.first.screenshot(type="png", scale="device")
            engine = self._get_ocr_engine()
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
                            if line.top < OCR_CANVAS_HEADER_PX * OCR_DEVICE_SCALE_FACTOR:
                                continue
                            if line.confidence < OCR_LOW_CONFIDENCE:
                                line = _retry_ocr_line(engine, tile, tile_top, line)
                            lines.append(line)
                    finally:
                        tile.close()
            lines = _deduplicate_ocr_lines(lines)
        except Exception as exc:
            raise WeReadError("本地 OCR 无法识别当前章节。") from exc
        texts = [line.text for line in lines]
        if not texts:
            raise WeReadError("本地 OCR 没有识别到当前章节正文。")
        return texts

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
    """Convert controller DOM payloads into cached single-line units."""

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

    def restore_window(self) -> None:
        self.controller.restore_window()

    def switch_book(self) -> WeReadChapter:
        self.restore_window()
        return self.load_current_chapter()

    def restore_chapter(self, chapter_url: str, chapter_id: str = "") -> WeReadChapter:
        self.controller.open_chapter_url(chapter_url, chapter_id)
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
            text = _text(paragraph)
            if text:
                units.extend(parse_text(text, self.max_chars))
        if not units:
            raise WeReadError("当前章节没有可阅读的正文。")

        book_id = _text(payload.get("book_id")) or _stable_id("book", book_title)
        chapter_id = _text(payload.get("chapter_id")) or _stable_id(
            "chapter", f"{chapter_url}\n{chapter_title}"
        )
        return WeReadChapter(
            book_id=book_id,
            book_title=book_title,
            chapter_id=chapter_id,
            chapter_title=chapter_title,
            chapter_url=chapter_url,
            units=tuple(units),
        )


def _text(value: Any) -> str:
    return " ".join(value.replace("\r", "\n").split()).strip() if isinstance(value, str) else ""


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


def _is_safe_weread_reader_url(url: str) -> bool:
    return bool(
        re.fullmatch(r"https://weread\.qq\.com/web/reader/[^\s]+", url.strip())
    )


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


_EXTRACT_CURRENT_CHAPTER = r"""
() => {
  const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const visible = (node) => {
    const style = window.getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' &&
      rect.width > 0 && rect.height > 0;
  };
  const chapter = document.querySelector('.readerChapterContent');
  if (!chapter) return null;

  const paragraphNodes = Array.from(chapter.querySelectorAll('p')).filter(visible);
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
    ], chapter) || firstText(['[class*="readerTopBar_chapter"]']),
    chapter_url: window.location.href,
    paragraphs
  };
}
"""
