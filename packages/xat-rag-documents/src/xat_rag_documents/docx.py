"""`DocxParser` (`docx-v2`): one section for each heading style. Tables become pipe rows.

The parser reads the WordprocessingML elements directly, so that it controls what is text:

- Text in tracked insertions, fields and content controls is kept. Deleted text and field codes are not.
- Runs that are hidden directly (`w:vanish`) or by their character or paragraph style are removed.
- Tables are read cell by cell from the XML. A `w:gridSpan` is never expanded into cells.
- Nested tables are kept inside their cell.

Zip checks run before the parse, and files with macros are refused. See ADR 0012.
"""

# lxml and the python-docx element classes have no full type data, so the "unknown" rules are off in
# this one module (docs/python/index.md). Only strings and sections leave the module.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# pyright: reportMissingTypeStubs=false, reportAttributeAccessIssue=false

import importlib.metadata
import re
import zipfile
from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

import docx
from docx.enum.style import WD_STYLE_TYPE
from xat_rag_core.errors import ParseError, ParseReason
from xat_rag_core.ids import heading_anchor
from xat_rag_core.model import BlockSpan, ParsedDocument, ParseReport, RawDocument, Section
from xat_rag_core.parsing import Deadline, source_document
from xat_rag_core.sanitize import sanitize_text

from xat_rag_documents.limits import DocumentLimits, check_size, limit_exceeded, parse_lock

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_HEADING = re.compile(r"Heading ([1-9])$")
_TAG = re.compile(rb"<Relationship\b[^>]*>")
_ATTRIBUTE = re.compile(rb'(\w+)="([^"]*)"')
_GRID_SPAN = re.compile(rb"gridSpan[^>]*?val=\"(\d{1,12})\"")
_MAX_GRID_SPAN = 64
_REMOVED_ANCESTORS = frozenset({f"{W}del", f"{W}moveFrom"})


def _main_part(archive: zipfile.ZipFile, names: list[str]) -> str:
    """Return the name of the main document part, from `_rels/.rels`."""
    if "_rels/.rels" not in names:
        raise ParseError(ParseReason.INVALID, "The DOCX file has no package relationships.")
    for tag in _TAG.findall(archive.read("_rels/.rels")):
        attributes = dict(_ATTRIBUTE.findall(tag))
        if attributes.get(b"Type", b"").endswith(b"/officeDocument"):
            return attributes.get(b"Target", b"").decode("utf-8", "replace").lstrip("/")
    raise ParseError(ParseReason.INVALID, "The DOCX file has no main document part.")


def check_zip(data: bytes, limits: DocumentLimits) -> list[str]:
    """Check the zip container before the parse, and return the part names. Raise `ParseError` on a problem."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > limits.max_zip_entries:
                raise limit_exceeded("max_zip_entries")
            if sum(info.file_size for info in infos) > limits.max_zip_uncompressed:
                raise limit_exceeded("max_zip_uncompressed")
            for info in infos:
                if info.file_size > 1_000_000 and info.file_size > limits.max_zip_ratio * max(info.compress_size, 1):
                    raise limit_exceeded("max_zip_ratio")
            names = [info.filename for info in infos]
            main = _main_part(archive, names)
            sizes = {info.filename: info.file_size for info in infos}
            if main not in sizes:
                raise ParseError(ParseReason.INVALID, "The main document part of the DOCX file is missing.")
            if sizes[main] > limits.max_docx_main_part:
                raise limit_exceeded("max_docx_main_part")
            content_types = archive.read("[Content_Types].xml") if "[Content_Types].xml" in names else b""
            if (
                any(name.lower().endswith("vbaproject.bin") for name in names)
                or b"macroenabled" in content_types.lower()
            ):
                raise ParseError(ParseReason.UNSUPPORTED_MEDIA_TYPE, "DOCX files with macros are refused.")
            for match in _GRID_SPAN.finditer(archive.read(main)):
                if int(match.group(1)) > _MAX_GRID_SPAN:
                    raise limit_exceeded("max_grid_span")
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, RuntimeError, OSError, KeyError) as error:
        raise ParseError(ParseReason.INVALID, "The DOCX file is not a valid zip file.") from error
    return names


def _on(element: Any) -> bool:
    """Return the value of an on/off property such as `w:vanish`."""
    if element is None:
        return False
    return element.get(f"{W}val", "true").lower() not in {"0", "false", "off"}


def _hidden_styles(document: Any) -> tuple[set[str], set[str]]:
    """Return the IDs of character styles and paragraph styles that hide their text, with base styles."""
    character: set[str] = set()
    paragraph: set[str] = set()
    for style in document.styles:
        current, hidden, depth = style, False, 0
        while current is not None and depth < 32 and not hidden:
            font = getattr(current, "font", None)
            hidden = bool(getattr(font, "hidden", False))
            current, depth = getattr(current, "base_style", None), depth + 1
        if hidden and style.type == WD_STYLE_TYPE.CHARACTER:
            character.add(style.style_id)
        elif hidden and style.type == WD_STYLE_TYPE.PARAGRAPH:
            paragraph.add(style.style_id)
    return character, paragraph


@dataclass(slots=True)
class _Reader:
    """Reads the text of WordprocessingML elements."""

    hidden_character: set[str]
    hidden_paragraph: set[str]
    tracked: bool = False

    def _removed(self, element: Any, stop: Any) -> bool:
        parent = element.getparent()
        while parent is not None and parent is not stop:
            if parent.tag in _REMOVED_ANCESTORS:
                return True
            if parent.tag in {f"{W}ins", f"{W}moveTo"}:
                self.tracked = True
            parent = parent.getparent()
        return False

    def _run_hidden(self, run: Any) -> bool:
        properties = run.find(f"{W}rPr")
        if properties is None:
            return False
        style = properties.find(f"{W}rStyle")
        return _on(properties.find(f"{W}vanish")) or (
            style is not None and style.get(f"{W}val") in self.hidden_character
        )

    def paragraph(self, element: Any) -> str:
        properties = element.find(f"{W}pPr")
        if properties is not None:
            style = properties.find(f"{W}pStyle")
            if style is not None and style.get(f"{W}val") in self.hidden_paragraph:
                return ""
        parts: list[str] = []
        for run in element.iter(f"{W}r"):
            if self._removed(run, element) or self._run_hidden(run):
                continue
            for child in run:
                if child.tag == f"{W}t":
                    parts.append(child.text or "")
                elif child.tag == f"{W}tab":
                    parts.append("\t")
                elif child.tag in {f"{W}br", f"{W}cr"}:
                    parts.append("\n")
                elif child.tag == f"{W}noBreakHyphen":
                    parts.append("-")
        return "".join(parts)

    def table(self, element: Any, depth: int = 0) -> str:
        if depth > 8:
            raise limit_exceeded("docx_table_depth")
        rows: list[str] = []
        for row in element.findall(f"{W}tr"):
            cells: list[str] = []
            for cell in row.findall(f"{W}tc"):
                parts = [
                    self.paragraph(child) if child.tag == f"{W}p" else self.table(child, depth + 1)
                    for child in cell
                    if child.tag in {f"{W}p", f"{W}tbl"}
                ]
                cells.append(" ".join(part.strip() for part in parts if part.strip()))
            rows.append("| " + " | ".join(cells) + " |")
        return "\n".join(rows)


def _blocks(body: Any, depth: int = 0) -> Iterator[Any]:
    """Return the paragraphs and tables of the body, also inside block content controls, in order."""
    for child in body:
        if child.tag in {f"{W}p", f"{W}tbl"}:
            yield child
        elif child.tag == f"{W}sdt" and depth < 8:
            content = child.find(f"{W}sdtContent")
            if content is not None:
                yield from _blocks(content, depth + 1)


@dataclass(slots=True)
class _Open:
    path: list[str]
    parts: list[str] = field(default_factory=list[str])
    units: list[tuple[int, int]] = field(default_factory=list[tuple[int, int]])
    length: int = 0

    def add(self, block: int, text: str) -> None:
        if self.parts:
            self.length += 1
        self.units.append((self.length, block))
        self.parts.append(text)
        self.length += len(text)


class DocxParser:
    """DOCX files (Office Open XML)."""

    def __init__(
        self, limits: DocumentLimits | None = None, lock: AbstractContextManager[object] | None = None
    ) -> None:
        """Make a parser. `lock` is the shared lock of binary parses."""
        self._limits = limits or DocumentLimits()
        self._lock = lock

    @property
    def kind(self) -> str:
        """The source kind."""
        return "docx"

    @property
    def version(self) -> str:
        """The parser version, with the python-docx version."""
        return f"docx-v2+python-docx{importlib.metadata.version('python-docx')}"

    def parse(self, raw: RawDocument, deadline: Deadline) -> ParsedDocument:
        """Parse the document."""
        check_size(raw.data, self._limits.max_binary_bytes)
        names = check_zip(raw.data, self._limits)
        with parse_lock(self._lock):
            deadline.restart()
            try:
                document = docx.Document(BytesIO(raw.data))
                character, paragraph = _hidden_styles(document)
                reader = _Reader(character, paragraph)
                style_names = {style.style_id: style.name or "" for style in document.styles}
                sections = self._sections(document.element.body, reader, style_names, deadline)
                core_title = document.core_properties.title or ""
            except ParseError:
                raise
            except Exception as error:  # python-docx and lxml raise many types on a bad file.
                raise ParseError(ParseReason.INVALID, "The DOCX file could not be read.") from error
        notes: list[str] = []
        if "word/comments.xml" in names:
            notes.append("comments_skipped")
        if any(name.startswith(("word/header", "word/footer")) for name in names):
            notes.append("headers_footers_skipped")
        if reader.tracked:
            notes.append("tracked_insertions_kept")
        title = raw.title or core_title or raw.doc_id
        built = tuple(self._build(title, item) for item in sections)
        return ParsedDocument(source_document(raw, title), built, ParseReport(notes=tuple(notes)))

    def _sections(self, body: Any, reader: _Reader, style_names: dict[str, str], deadline: Deadline) -> list[_Open]:
        sections = [_Open(path=[])]
        path: list[tuple[int, str]] = []
        characters = 0
        for block, element in enumerate(_blocks(body), start=1):
            if block % 64 == 0:
                deadline.check()
            if element.tag == f"{W}tbl":
                text = sanitize_text(reader.table(element))
            else:
                text = sanitize_text(reader.paragraph(element)).strip()
                properties = element.find(f"{W}pPr")
                style = properties.find(f"{W}pStyle") if properties is not None else None
                name = style_names.get(style.get(f"{W}val"), "") if style is not None else ""
                match = _HEADING.match(name)
                if match and text:
                    level = int(match.group(1))
                    path = [entry for entry in path if entry[0] < level] + [(level, " ".join(text.split()))]
                    sections.append(_Open(path=[heading for _, heading in path]))
                    continue
            if text:
                characters += len(text)
                if characters > self._limits.max_document_chars:
                    raise limit_exceeded("max_document_chars")
                sections[-1].add(block, text)
        return sections

    @staticmethod
    def _build(title: str, item: _Open) -> Section:
        header = " > ".join([title, *item.path])
        if not item.parts:  # An empty section stays, so that later IDs do not change.
            return Section(heading_anchor(item.path), "section", header, "", BlockSpan(0, 0))
        first, last = item.units[0][1], item.units[-1][1]
        return Section(
            anchor=heading_anchor(item.path),
            kind="section",
            header=header,
            text="\n".join(item.parts),
            span=BlockSpan(first, last),
            units=tuple(item.units),
        )
