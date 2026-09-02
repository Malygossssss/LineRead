"""Text sources and source-line parsing for the desktop reader."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


DEFAULT_MAX_CHARS = 40

class ReaderSource(ABC):
    """Abstract source that supplies display-ready reading units."""

    @abstractmethod
    def get_units(self) -> list[str]:
        """Return non-empty reading units."""


class TxtSource(ReaderSource):
    """Load a local UTF-8 TXT file and turn it into reading units."""

    def __init__(self, path: str | Path, max_chars: int = DEFAULT_MAX_CHARS) -> None:
        self.path = Path(path).expanduser()
        self.max_chars = max_chars

    def get_units(self) -> list[str]:
        if not self.path.exists():
            raise FileNotFoundError(f"TXT 文件不存在：{self.path}")
        if not self.path.is_file():
            raise ValueError(f"路径不是文件：{self.path}")

        try:
            text = self.path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("TXT 文件不是有效的 UTF-8 编码。") from exc
        except OSError as exc:
            raise OSError(f"无法读取 TXT 文件：{exc}") from exc

        units = parse_text(text, self.max_chars)
        if not units:
            raise ValueError("TXT 文件没有可阅读内容。")
        return units


def parse_text(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    """Return cleaned, non-empty physical lines without further splitting."""

    if max_chars < 1:
        raise ValueError("max_chars 必须大于 0")

    return _clean_lines(text)


def _clean_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = " ".join(raw_line.replace("\u3000", " ").split()).strip()
        if line:
            lines.append(line)
    return lines
