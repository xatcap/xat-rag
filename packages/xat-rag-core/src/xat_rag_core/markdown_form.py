"""The one Markdown form of a document: typed blocks, the Markdown rendered from them, and the source positions.

Readers make `SourceBlock` values. Markdown is rendered from the blocks and never parsed back, so text of a source
file cannot make a heading or a table column. Each chunk text is a slice of this Markdown.
"""

import bisect
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from xat_rag_core.ids import heading_anchor
from xat_rag_core.model import BlockKind, Section, Span, TextBlock

_BLOCK_SEPARATOR = "\n\n"
_MARKER = re.compile(r"^(#{1,6}(?=\s|$)|[-+*](?=\s|$)|>|\d{1,9}[.)](?=\s|$)|`{3,}|~{3,}|\||(?:[-*_]\s*){3,}$)")


class SourceBlockKind(StrEnum):
    """The kind of a block that a reader makes."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    CODE = "code"


@dataclass(frozen=True, slots=True)
class SourceBlock:
    """A block of a source document.

    `segments` holds the text with the unit (page, block or row) of each part. A paragraph that crosses a page has
    two segments. A table has `rows` instead. The first row is the header. `row_units` has one unit for each row.
    """

    kind: SourceBlockKind
    segments: tuple[tuple[str, int], ...] = ()
    level: int = 0
    rows: tuple[tuple[str, ...], ...] = ()
    row_units: tuple[int, ...] = ()
    ordered: bool = False


@dataclass(frozen=True, slots=True)
class _Rendered:
    kind: SourceBlockKind
    start: int
    end: int
    level: int
    heading: str


@dataclass(frozen=True, slots=True)
class MarkdownForm:
    """Rendered Markdown, with its blocks and the map from character offsets to units."""

    text: str
    blocks: tuple[_Rendered, ...]
    units: tuple[tuple[int, int], ...]


def escape_line(text: str) -> str:
    """Return `text` as one line that cannot start a Markdown block."""
    line = " ".join(text.split())
    return f"\\{line}" if _MARKER.match(line) else line


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("\\", "\\\\").replace("|", "\\|")


def _table_line(row: Sequence[str], width: int) -> str:
    cells = [_cell(cell) for cell in row] + [""] * (width - len(row))
    return "| " + " | ".join(cells) + " |"


class _Writer:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.size = 0
        self.units: list[tuple[int, int]] = []

    def write(self, text: str, unit: int | None = None) -> None:
        if unit is not None and (not self.units or self.units[-1][1] != unit):
            self.units.append((self.size, unit))
        self.parts.append(text)
        self.size += len(text)


def _write_table(writer: _Writer, block: SourceBlock) -> None:
    width = max(len(row) for row in block.rows)
    units = block.row_units or tuple(block.segments[0][1] for _ in block.rows)
    writer.write(_table_line(block.rows[0], width), units[0])
    writer.write("\n| " + " | ".join(["---"] * width) + " |")
    for row, unit in zip(block.rows[1:], units[1:], strict=True):
        writer.write("\n")
        writer.write(_table_line(row, width), unit)


def _write_code(writer: _Writer, block: SourceBlock) -> None:
    text = "".join(part for part, _ in block.segments).strip("\n")
    longest = max((len(run) for run in re.findall(r"`{3,}", text)), default=2)
    fence = "`" * (longest + 1)
    writer.write(fence + "\n", block.segments[0][1])
    writer.write(text + "\n" + fence)


def _write_text(writer: _Writer, block: SourceBlock) -> None:
    prefix = ""
    if block.kind is SourceBlockKind.HEADING:
        prefix = "#" * max(1, min(block.level, 6)) + " "
    elif block.kind is SourceBlockKind.LIST_ITEM:
        prefix = "1. " if block.ordered else "- "
    writer.write(prefix, block.segments[0][1])
    first = True
    for text, unit in block.segments:
        line = " ".join(text.split())
        if not line:
            continue
        if first:
            writer.write(escape_line(line) if not prefix else line, unit)
        else:
            writer.write(" ")
            writer.write(line, unit)
        first = False


def _has_content(block: SourceBlock) -> bool:
    if block.kind is SourceBlockKind.TABLE:
        return bool(block.rows)
    return any(text.strip() for text, _ in block.segments)


def render_markdown(blocks: Sequence[SourceBlock]) -> MarkdownForm:
    """Render the blocks as Markdown. Blocks with no text are left out."""
    writer = _Writer()
    rendered: list[_Rendered] = []
    for block in filter(_has_content, blocks):
        if rendered:
            writer.write(_BLOCK_SEPARATOR)
        start = writer.size
        if block.kind is SourceBlockKind.TABLE:
            _write_table(writer, block)
        elif block.kind is SourceBlockKind.CODE:
            _write_code(writer, block)
        else:
            _write_text(writer, block)
        heading = " ".join(" ".join(t for t, _ in block.segments).split())
        rendered.append(_Rendered(block.kind, start, writer.size, block.level, heading))
    return MarkdownForm("".join(writer.parts), tuple(rendered), tuple(writer.units))


def section_header(title: str, path: Sequence[str]) -> str:
    """Return the header of a section: the title and the heading path."""
    names = list(path[1:] if path and path[0] == title else path)  # The title is often the first heading.
    return " > ".join([title, *names]) if title else " > ".join(names)


def _unit_at(units: Sequence[tuple[int, int]], offset: int) -> int:
    index = bisect.bisect_right(units, offset, key=lambda item: item[0])
    return units[max(index - 1, 0)][1]


def _section(
    form: MarkdownForm,
    blocks: Sequence[_Rendered],
    *,
    anchor: str,
    header: str,
    before: int,
    make_span: Callable[[int, int], Span],
) -> Section:
    if not blocks:
        unit = _unit_at(form.units, before) if form.units else 1
        return Section(anchor, "section", header, "", make_span(unit, unit))
    start, end = blocks[0].start, blocks[-1].end
    units = [(0, _unit_at(form.units, start))]
    low = bisect.bisect_right(form.units, start, key=lambda item: item[0])
    high = bisect.bisect_left(form.units, end, key=lambda item: item[0])
    units += [(offset - start, unit) for offset, unit in form.units[low:high]]
    return Section(
        anchor=anchor,
        kind="section",
        header=header,
        text=form.text[start:end],
        span=make_span(min(unit for _, unit in units), max(unit for _, unit in units)),
        units=tuple(units),
        blocks=tuple(TextBlock(BlockKind(b.kind.value), b.start - start, b.end - start) for b in blocks),
    )


def build_sections(form: MarkdownForm, title: str, make_span: Callable[[int, int], Span]) -> tuple[Section, ...]:
    """Return one section for the text before the first heading, and one for each heading.

    `make_span` makes the span of a section from its first and its last unit.
    """
    sections: list[Section] = []
    path: list[tuple[int, str]] = []
    anchor, header, before = "h", section_header(title, []), 0
    body: list[_Rendered] = []
    for block in form.blocks:
        if block.kind is not SourceBlockKind.HEADING:
            body.append(block)
            continue
        sections.append(_section(form, body, anchor=anchor, header=header, before=before, make_span=make_span))
        path = [item for item in path if item[0] < block.level] + [(block.level, block.heading)]
        names = [name for _, name in path]
        anchor, header, before, body = heading_anchor(names), section_header(title, names), block.end, []
    sections.append(_section(form, body, anchor=anchor, header=header, before=before, make_span=make_span))
    return tuple(sections)
