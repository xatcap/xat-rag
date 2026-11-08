"""Text sanitizing. It runs on the extracted text of each document, before chunking. See ADR 0010."""

import re
import unicodedata
from collections.abc import Mapping
from types import MappingProxyType

# CSI sequences, string sequences (OSC, DCS, SOS, PM, APC) with or without an end, and other escapes.
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b[\]PX^_][^\x07\x1b]*(?:\x07|\x1b\\|$)|\x1b[@-Z\\-_]")

_KEEP = frozenset({0x09, 0x0A, 0x0D})
_REMOVED: tuple[int, ...] = (
    *(c for c in range(0x00, 0x20) if c not in _KEEP),
    0x7F,
    *(c for c in range(0x80, 0xA0) if c != 0x85),
    # Zero-width and invisible format characters.
    0x200B,
    0x200C,
    0x200D,
    0x2060,
    0x2061,
    0x2062,
    0x2063,
    0x2064,
    0xFEFF,
    0x180E,
    0x00AD,
    # Bidirectional controls and marks.
    0x200E,
    0x200F,
    0x061C,
    *range(0x202A, 0x202F),
    *range(0x2066, 0x206A),
    # Unicode tag characters and variation selectors, which can hide data.
    *range(0xE0000, 0xE0080),
    *range(0xFE00, 0xFE10),
    *range(0xE0100, 0xE01F0),
    *range(0x206A, 0x2070),
    *range(0xFFF9, 0xFFFC),
)
_TABLE: Mapping[int, str | None] = MappingProxyType({**dict.fromkeys(_REMOVED), 0x85: "\n", 0x2028: "\n", 0x2029: "\n"})


def sanitize_metadata[V](metadata: Mapping[str, V]) -> dict[str, V]:
    """Return the metadata with each string value sanitized. Other values stay."""
    result: dict[str, V] = {}
    for name, value in metadata.items():
        result[name] = sanitize_text(value) if isinstance(value, str) else value  # pyright: ignore[reportArgumentType]
    return result


def sanitize_text(text: str) -> str:
    """Remove ANSI codes, control, zero-width, bidirectional, tag and variation characters. Normalize to NFC.

    Tab and line feed stay. Carriage returns, NEL, and the line and paragraph separators become
    line feeds. Zero-width joiners are also removed. This can change emoji sequences and some
    scripts, which is an accepted cost against hidden text.
    """
    without_ansi = _ANSI.sub("", text)
    cleaned = without_ansi.translate(_TABLE).replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", cleaned)
