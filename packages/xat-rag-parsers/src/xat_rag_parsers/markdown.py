"""`MarkdownParser` (`markdown-v1`): one section for each top-level heading.

Headings inside lists or block quotes are text, not section starts. The heading path is the plain
text of the headings, without inline markup.
"""

from markdown_it import MarkdownIt
from markdown_it.token import Token
from xat_rag_core.ids import heading_anchor
from xat_rag_core.model import LineSpan, ParsedDocument, RawDocument, Section
from xat_rag_core.parsing import Deadline, decode_text, source_document
from xat_rag_core.sanitize import sanitize_text


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
        return "markdown-v2"

    def parse(self, raw: RawDocument, deadline: Deadline) -> ParsedDocument:
        """Parse the document."""
        text = sanitize_text(decode_text(raw.data))
        lines = text.split("\n")
        tokens = MarkdownIt("commonmark").parse(text)
        headings: list[tuple[int, int, int, str]] = []  # (level, first line, line after, plain text)
        for index, token in enumerate(tokens):
            if token.type == "heading_open" and token.map is not None and token.level == 0:
                inline = tokens[index + 1] if index + 1 < len(tokens) else None
                headings.append((int(token.tag[1]), token.map[0], token.map[1], _plain(inline)))
        title = raw.title or next((h[3] for h in headings if h[0] == 1), "") or raw.doc_id

        sections: list[Section] = []
        starts = [h[1] for h in headings] + [len(lines)]
        body, span = _body(lines, 0, starts[0])
        sections.append(Section("h", "section", _header(title, []), body, span))
        path: list[tuple[int, str]] = []
        for number, (level, _first, after, heading) in enumerate(headings):
            deadline.check()
            path = [item for item in path if item[0] < level] + [(level, heading)]
            names = [name for _, name in path]
            body, span = _body(lines, after, starts[number + 1])
            sections.append(
                Section(
                    anchor=heading_anchor(names),
                    kind="section",
                    header=_header(title, names),
                    text=body,
                    span=span,
                    metadata={"heading_level": level},
                )
            )
        return ParsedDocument(source_document(raw, title), tuple(sections))
