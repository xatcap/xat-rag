"""`HtmlParser` (`html-v2`): one section for each top-level `h1` to `h6`.

The walk over the tree is linear: the block, heading and `pre` context of each element is found
once, from its parent. Active content, metadata and hidden elements are removed. Inline elements
keep the spacing of the source, and `pre` keeps its line breaks. Line spans are lines of the HTML
source (`Tag.sourceline` of the element that holds the text). Nothing is fetched. See ADR 0012.
"""

import re
from contextlib import AbstractContextManager
from dataclasses import dataclass, field

import bs4
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString
from xat_rag_core.ids import heading_anchor
from xat_rag_core.model import LineSpan, ParsedDocument, RawDocument, Section
from xat_rag_core.parsing import Deadline, source_document
from xat_rag_core.sanitize import sanitize_text

from xat_rag_documents.limits import DocumentLimits, check_size, limit_exceeded, parse_lock

_REMOVED_TAGS = (
    "script",
    "style",
    "noscript",
    "template",
    "iframe",
    "svg",
    "math",
    "object",
    "embed",
    "canvas",
    "meta",
    "link",
    "title",
    "base",
)
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_BLOCKS = frozenset(
    {
        "p",
        "div",
        "li",
        "tr",
        "pre",
        "blockquote",
        "section",
        "article",
        "header",
        "footer",
        "main",
        "nav",
        "aside",
        "td",
        "th",
        "dd",
        "dt",
        "table",
        "ul",
        "ol",
        "dl",
        "figure",
        "figcaption",
        "form",
        "fieldset",
        "address",
        "details",
        "summary",
        "hr",
        "br",
        "body",
        "html",
        "head",
    }
)
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?:\.0*)?\s*(?:;|$)|font-size\s*:\s*0", re.IGNORECASE
)
_SPACE = re.compile(r"[ \t\r\n\f]+")


def _is_hidden(tag: Tag) -> bool:
    if tag.has_attr("hidden") or str(tag.get("aria-hidden", "")).lower() == "true":
        return True
    return bool(_HIDDEN_STYLE.search(str(tag.get("style", ""))))


def _page_title(soup: BeautifulSoup) -> str:
    """Return the text of the first `title` that is not inside an SVG or MathML element."""
    for title in soup.find_all("title"):
        if title.find_parent(["svg", "math"]) is None:
            return " ".join(title.get_text().split())
    return ""


@dataclass(slots=True)
class _Open:
    path: list[str]
    parts: list[str] = field(default_factory=list[str])
    units: list[tuple[int, int]] = field(default_factory=list[tuple[int, int]])
    length: int = 0
    block: int | None = None

    def add(self, text: str, line: int, block: int) -> None:
        if self.parts and block != self.block:
            self.parts.append("\n")
            self.length += 1
        self.block = block
        self.units.append((self.length, line))
        self.parts.append(text)
        self.length += len(text)


def _add_text(section: _Open, raw_text: str, outer: "_Context") -> int:
    """Add the text of one string node, and return the number of characters added."""
    text = sanitize_text(raw_text)
    if not outer.pre:
        text = _SPACE.sub(" ", text)
    if text.strip():
        section.add(text, outer.line, outer.block)
        return len(text)
    if section.parts and section.block == outer.block and not section.parts[-1].endswith(" "):
        section.parts.append(" ")  # White space between inline elements stays one space.
        section.length += 1
    return 0


@dataclass(slots=True)
class _Context:
    block: int
    line: int
    heading: Tag | None
    pre: bool


class HtmlParser:
    """HTML documents, parsed with the standard `html.parser`."""

    def __init__(
        self, limits: DocumentLimits | None = None, lock: AbstractContextManager[object] | None = None
    ) -> None:
        """Make a parser. `lock` is the shared lock of binary parses."""
        self._limits = limits or DocumentLimits()
        self._lock = lock

    @property
    def kind(self) -> str:
        """The source kind."""
        return "html"

    @property
    def version(self) -> str:
        """The parser version, with the beautifulsoup4 version."""
        return f"html-v2+bs4{bs4.__version__}"

    def parse(self, raw: RawDocument, deadline: Deadline) -> ParsedDocument:
        """Parse the document."""
        check_size(raw.data, self._limits.max_html_bytes)
        # Each element starts with "<", so this cheap count is an upper bound that stops the parse early.
        if raw.data.count(b"<") > self._limits.max_html_elements:
            raise limit_exceeded("max_html_elements")
        with parse_lock(self._lock):
            deadline.restart()
            try:
                soup = BeautifulSoup(raw.data, "html.parser")
                return self._parse(raw, soup, deadline)
            except RecursionError as error:
                raise limit_exceeded("html_depth") from error

    def _parse(self, raw: RawDocument, soup: BeautifulSoup, deadline: Deadline) -> ParsedDocument:
        page_title = _page_title(soup)
        for tag in soup.find_all(_REMOVED_TAGS):
            if not tag.decomposed:
                tag.decompose()
        for tag in [tag for tag in soup.find_all(True) if _is_hidden(tag)]:
            if not tag.decomposed:
                tag.decompose()
        title = raw.title or sanitize_text(page_title) or raw.doc_id

        sections = [_Open(path=[])]
        path: list[tuple[int, str]] = []
        contexts: dict[int, _Context] = {id(soup): _Context(block=0, line=1, heading=None, pre=False)}
        characters = 0
        for count, node in enumerate(soup.descendants):
            if count % 1024 == 0:
                deadline.check()
            parent = node.parent
            outer = contexts.get(id(parent)) if parent is not None else None
            if outer is None:
                continue
            if isinstance(node, Tag):
                contexts[id(node)] = self._context(node, outer, len(contexts))
                if node.name in _HEADINGS and outer.heading is None:
                    heading = " ".join(sanitize_text(node.get_text(" ")).split())
                    if heading:
                        level = int(node.name[1])
                        path = [entry for entry in path if entry[0] < level] + [(level, heading)]
                        sections.append(_Open(path=[name for _, name in path]))
                continue
            if type(node) is not NavigableString or outer.heading is not None:
                continue  # Comments, CDATA and doctype are left out. Heading text is in the path.
            characters += _add_text(sections[-1], str(node), outer)
            if characters > self._limits.max_document_chars:
                raise limit_exceeded("max_document_chars")
        built = tuple(self._build(title, item) for item in sections)
        return ParsedDocument(source_document(raw, title), built)

    @staticmethod
    def _context(tag: Tag, outer: _Context, number: int) -> _Context:
        line = tag.sourceline if tag.sourceline is not None else outer.line
        block = number if tag.name in _BLOCKS or tag.name in _HEADINGS else outer.block
        heading = tag if tag.name in _HEADINGS and outer.heading is None else outer.heading
        return _Context(block=block, line=line, heading=heading, pre=outer.pre or tag.name == "pre")

    @staticmethod
    def _build(title: str, item: _Open) -> Section:
        header = " > ".join([title, *item.path])
        joined = "".join(item.parts)
        text = joined.strip()
        if not text:  # An empty section stays, so that later IDs do not change.
            return Section(heading_anchor(item.path), "section", header, "", LineSpan(1, 1))
        lead = len(joined) - len(joined.lstrip())
        units = tuple((max(0, offset - lead), line) for offset, line in item.units)
        unit_lines = [line for _, line in item.units]
        return Section(
            anchor=heading_anchor(item.path),
            kind="section",
            header=header,
            text=text,
            span=LineSpan(min(unit_lines), max(unit_lines)),
            units=units,
        )
