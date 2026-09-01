"""Text sources and punctuation-aware parsing for the desktop reader."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path


DEFAULT_MAX_CHARS = 40

_STRONG_END = re.compile(r"(?:……|…{2,}|\.{6}|[。！？；!?;])")
_WEAK_END = re.compile(r"[，：、,:]")
_ASCII_WORD_EDGE = re.compile(r"[A-Za-z0-9]$")
_ASCII_WORD_START = re.compile(r"^[A-Za-z0-9]")


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
    """Clean text and split it into compact, punctuation-aware units.

    Strong sentence endings are preferred. Only sentences that exceed
    ``max_chars`` are split again at weak punctuation, then hard-split as a
    final safeguard so a label never needs to wrap.
    """

    if max_chars < 1:
        raise ValueError("max_chars 必须大于 0")

    cleaned = _clean_text(text)
    if not cleaned:
        return []

    units: list[str] = []
    for sentence in _split_including_delimiter(cleaned, _STRONG_END):
        if len(sentence) <= max_chars:
            units.append(sentence)
        else:
            units.extend(_split_long_sentence(sentence, max_chars))
    return [unit for unit in units if unit]


def _clean_text(text: str) -> str:
    lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"[\t\f\v \u3000]+", " ", raw_line).strip()
        if line:
            lines.append(line)

    if not lines:
        return ""

    result = lines[0]
    for line in lines[1:]:
        separator = " " if _needs_word_separator(result, line) else ""
        result += separator + line
    return result.strip()


def _needs_word_separator(left: str, right: str) -> bool:
    return bool(_ASCII_WORD_EDGE.search(left) and _ASCII_WORD_START.search(right))


def _split_including_delimiter(text: str, delimiter: re.Pattern[str]) -> list[str]:
    pieces: list[str] = []
    start = 0
    for match in delimiter.finditer(text):
        end = match.end()
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        start = end
    tail = text[start:].strip()
    if tail:
        pieces.append(tail)
    return pieces


def _split_long_sentence(sentence: str, max_chars: int) -> list[str]:
    clauses = _split_including_delimiter(sentence, _WEAK_END)
    if len(clauses) == 1:
        return _hard_split(sentence, max_chars)

    result: list[str] = []
    current = ""
    for clause in clauses:
        for fragment in _hard_split(clause, max_chars):
            if not current:
                current = fragment
            elif len(current) + len(fragment) <= max_chars:
                current += fragment
            else:
                result.append(current)
                current = fragment
    if current:
        result.append(current)
    return result


def _hard_split(text: str, max_chars: int) -> list[str]:
    return [text[index : index + max_chars] for index in range(0, len(text), max_chars)]
