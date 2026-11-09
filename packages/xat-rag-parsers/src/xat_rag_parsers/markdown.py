"""`MarkdownParser` (`markdown-v3`): one section for each top-level heading.

Headings inside lists or block quotes are text, not section starts. The heading path is the plain
text of the headings, without inline markup.
"""

import bisect
import itertools

from markdown_it import MarkdownIt
from markdown_it.token import Token
from xat_rag_core.errors import ParseError, ParseReason
from xat_rag_core.ids import heading_anchor
from xat_rag_core.model import BlockKind, LineSpan, ParsedDocument, RawDocument, Section, TextBlock
from xat_rag_core.parsing import Deadline, decode_text, source_document
from xat_rag_core.sanitize import sanitize_text

MAX_CHARS = 20_000_000
"""The largest text that goes to the Markdown parser, because the parser has no deadline check."""

_BLOCK_KINDS = {
    "paragraph_open": BlockKind.PARAGRAPH,
    "blockquote_open": BlockKind.PARAGRAPH,
    "html_block": BlockKind.PARAGRAPH,
    "table_open": BlockKind.TABLE,
    "fence": BlockKind.CODE,
    "code_block": BlockKind.CODE,
}
type _LineBlock = tuple[BlockKind, int, int]


def _line_blocks(tokens: list[Token]) -> list[_LineBlock]:
    """Return the top-level blocks as lines. Each item of a top-level list is a block."""
    found: list[_LineBlock] = []
    for token in tokens:
        if token.map is None:
            continue
        if token.level == 0 and token.type in _BLOCK_KINDS:
            found.append((_BLOCK_KINDS[token.type], token.map[0], token.map[1]))
        elif token.level == 1 and token.type == "list_item_open":
            found.append((BlockKind.LIST_ITEM, token.map[0], token.map[1]))
    return found


def _between(blocks: list[_LineBlock], first: int, end: int) -> list[_LineBlock]:
    """Return the blocks that start in the lines `first..end`. `blocks` is in document order."""
    low = bisect.bisect_left(blocks, first, key=lambda item: item[1])
    return blocks[low : bisect.bisect_left(blocks, end, key=lambda item: item[1])]


def _text_blocks(body: str, blocks: list[_LineBlock], offsets: list[int], first_line: int) -> tuple[TextBlock, ...]:
    """Return the blocks of a section body as character offsets in the body."""
    result: list[TextBlock] = []
    base = offsets[first_line]
    for kind, line, end_line in blocks:
        start = max(offsets[line] - base, 0)
        end = min(offsets[end_line] - base, len(body))
        end = start + len(body[start:end].rstrip())
        if end > start:
            result.append(TextBlock(kind, start, end))
    return tuple(result)


def _plain(inline: Token | None) -> str:
    if inline is None:
        return ""
    parts = [child.content for child in inline.children or [] if child.type in {"text", "code_inline"}]
    return " ".join("".join(parts).split())


def _header(title: str, path: list[str]) -> str:
    names = path[1:] if path and path[0] == title else path  # The title is often the first heading.
    return " > ".join([title, *names]) if title else " > ".join(names)


def _body(lines: list[str], first: int, end: int) -> tuple[str, LineSpan]:
    """Return the text of lines[first:end] without blank edges, and its 1-based line span."""
    while first < end and not lines[first].strip():
        first += 1
    while end > first and not lines[end - 1].strip():
        end -= 1
    return "\n".join(lines[first:end]), LineSpan(first + 1, max(first + 1, end))


class MarkdownParser:
    """Splits Markdown by headings. The header of each section is the title and the heading path."""

    @property
    def kind(self) -> str:
        """The source kind."""
        return "markdown"

    @property
    def version(self) -> str:
        """The parser version."""
        return "markdown-v3"

    def parse(self, raw: RawDocument, deadline: Deadline) -> ParsedDocument:
        """Parse the document."""
        text = sanitize_text(decode_text(raw.data))
        lines = text.split("\n")
        if len(text) > MAX_CHARS:
            raise ParseError(ParseReason.LIMIT_EXCEEDED, "The Markdown document is too large.")
        tokens = MarkdownIt("commonmark").enable("table").parse(text)
        blocks = _line_blocks(tokens)
        offsets = [0, *itertools.accumulate(len(line) + 1 for line in lines)]
        headings: list[tuple[int, int, int, str]] = []  # (level, first line, line after, plain text)
        for index, token in enumerate(tokens):
            if token.type == "heading_open" and token.map is not None and token.level == 0:
                inline = tokens[index + 1] if index + 1 < len(tokens) else None
                headings.append((int(token.tag[1]), token.map[0], token.map[1], _plain(inline)))
        title = raw.title or next((h[3] for h in headings if h[0] == 1), "") or raw.doc_id

        sections: list[Section] = []
        starts = [h[1] for h in headings] + [len(lines)]
        body, span = _body(lines, 0, starts[0])
        inside = _between(blocks, 0, starts[0])
        sections.append(
            Section(
                "h",
                "section",
                _header(title, []),
                body,
                span,
                blocks=_text_blocks(body, inside, offsets, span.line_start - 1),
            )
        )
        path: list[tuple[int, str]] = []
        for number, (level, _first, after, heading) in enumerate(headings):
            deadline.check()
            path = [item for item in path if item[0] < level] + [(level, heading)]
            names = [name for _, name in path]
            body, span = _body(lines, after, starts[number + 1])
            inside = _between(blocks, after, starts[number + 1])
            sections.append(
                Section(
                    anchor=heading_anchor(names),
                    kind="section",
                    header=_header(title, names),
                    text=body,
                    span=span,
                    metadata={"heading_level": level},
                    blocks=_text_blocks(body, inside, offsets, span.line_start - 1),
                )
            )
        return ParsedDocument(source_document(raw, title), tuple(sections))
