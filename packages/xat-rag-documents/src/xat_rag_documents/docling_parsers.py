"""Parsers for PDF, DOCX, HTML and Excel files, on top of Docling.

Docling finds the layout: headings, paragraphs, lists, tables, reading order, and page headers and footers. Each item
becomes a `SourceBlock`, with its source position. The Markdown is rendered from the blocks. Docling's own Markdown
export is not used, because it has no source positions. PDF files use the layout models from the models folder.
Nothing is downloaded, and OCR is off.
"""

# Docling has no full type data, so the "unknown" rules are off in this one module (docs/portable/python/index.md).
# Only `ParsedDocument` values leave the module.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# pyright: reportMissingTypeStubs=false, reportAttributeAccessIssue=false, reportUnknownParameterType=false
# pyright: reportMissingParameterType=false, reportUnknownLambdaType=false, reportPrivateImportUsage=false, reportPrivateUsage=false
# pyright: reportOptionalMemberAccess=false

import importlib.metadata
import logging
import math
import re
import time
from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import replace
from functools import lru_cache
from io import BytesIO
from itertools import chain
from pathlib import Path
from typing import Any

from xat_rag_core.errors import ConfigError, ParseError, ParseReason
from xat_rag_core.markdown_form import SourceBlock, SourceBlockKind, build_sections, render_markdown
from xat_rag_core.model import (
    BlockSpan,
    CellSpan,
    PageSpan,
    ParsedDocument,
    ParseReport,
    RawDocument,
    Section,
    SkippedPage,
    Span,
)
from xat_rag_core.parsing import Deadline, source_document
from xat_rag_core.sanitize import sanitize_text

from xat_rag_documents.limits import DocumentLimits, check_size, limit_exceeded, parse_lock
from xat_rag_documents.zip_checks import check_zip

_LOG = logging.getLogger(__name__)

PDF_SECONDS_PER_PAGE = 2.0
"""The time for one PDF page. The deadline of a PDF is this time for each page, or the normal deadline if larger."""

LAYOUT_REVISION = "8f39ad3c0b4c"
"""The first 12 characters of the locked revision of the layout model. It goes into the PDF parser version."""

TABLE_REVISION = "fc0f2d45e221"
"""The first 12 characters of the locked revision of the table model. It goes into the PDF parser version."""

_HEADING_SIZE_STEP = 1.5
_SENTENCE_ENDS = (".", "!", "?", ":", ";")
_NO_SPACE_BEFORE = re.compile(r"\s+([,.;:!?)])")


def longest_parse_seconds(limits: DocumentLimits | None = None) -> float:
    """Return the deadline of the largest PDF file that the limits allow."""
    return (limits or DocumentLimits()).max_pdf_pages * PDF_SECONDS_PER_PAGE


def _docling_version() -> str:
    return importlib.metadata.version("docling")


@lru_cache(maxsize=8)
def _converter(models: Path | None, suffix: str) -> Any:
    """Return the shared converter of one format. Docling loads the models once, on the first PDF file.

    Docling chooses a backend from the content of a file. Each converter allows only one format, so that a file
    uploaded as HTML cannot run the PDF pipeline with no page limit.
    """
    # The import is slow, and the service does not need Docling until the first document.
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions  # noqa: PLC0415
    from docling.datamodel.base_models import InputFormat  # noqa: PLC0415
    from docling.datamodel.pipeline_options import PdfPipelineOptions  # noqa: PLC0415
    from docling.document_converter import DocumentConverter, PdfFormatOption  # noqa: PLC0415

    options = PdfPipelineOptions(
        artifacts_path=str(models) if models is not None else None,
        do_ocr=False,
        do_table_structure=True,
        accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU),
    )
    allowed = {".pdf": InputFormat.PDF, ".docx": InputFormat.DOCX, ".html": InputFormat.HTML, ".xlsx": InputFormat.XLSX}
    return DocumentConverter(
        allowed_formats=[allowed[suffix]], format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )


def _convert(data: bytes, name: str, timeout: float | None, models: Path | None) -> Any:
    from docling.datamodel.base_models import DocumentStream, InputFormat  # noqa: PLC0415

    converter = _converter(models, Path(name).suffix)
    if name.endswith(".pdf"):
        # Docling keeps one pipeline for each set of options. The timeout changes for each file, so it goes on a
        # copy of the options of the pipeline, and the models load only once.
        pipeline = converter._get_pipeline(InputFormat.PDF)
        pipeline.pipeline_options = pipeline.pipeline_options.model_copy(update={"document_timeout": timeout})
    return converter.convert(DocumentStream(name=name, stream=BytesIO(data)), raises_on_error=False)


def _clean(text: str) -> str:
    return " ".join(sanitize_text(text).split())


def _segments(item: Any, unit_of) -> tuple[tuple[str, int], ...]:
    """Return the text of an item, cut at its page changes."""
    text = item.text or ""
    provenance = getattr(item, "prov", None) or []
    if len(provenance) < 2 or provenance[0].charspan[0] != 0 or provenance[-1].charspan[1] != len(text):
        return ((sanitize_text(text), unit_of(item)),)
    return tuple((sanitize_text(text[p.charspan[0] : p.charspan[1]]), p.page_no) for p in provenance)


def _row_texts(row: Any) -> tuple[str, ...]:
    return tuple(sanitize_text(cell.text or "") for cell in row)


def _rows(item: Any) -> tuple[tuple[str, ...], ...]:
    return tuple(map(_row_texts, item.data.grid))


def _inline_text(group: Any, document: Any) -> tuple[str, list[str]]:
    """Return the joined text of an inline group, and the references of its parts."""
    parts = [child.resolve(document) for child in group.children]
    text = " ".join(_clean(part.text) for part in parts if getattr(part, "text", ""))
    return _NO_SPACE_BEFORE.sub(r"\1", text), [part.self_ref for part in parts]


def _inline_child(item: Any, document: Any) -> Any | None:
    from docling_core.types.doc.document import InlineGroup  # noqa: PLC0415

    children = [ref.resolve(document) for ref in item.children]
    return next((child for child in children if isinstance(child, InlineGroup)), None)


def _inline_block(group: Any, document: Any, unit_of, used: set[str]) -> SourceBlock:
    text, parts = _inline_text(group, document)
    used.update(parts)
    first = group.children[0].resolve(document) if group.children else group
    return SourceBlock(SourceBlockKind.PARAGRAPH, ((text, unit_of(first)),))


def _list_block(item: Any, document: Any, unit_of, used: set[str]) -> SourceBlock:
    inline = None if item.text else _inline_child(item, document)
    if inline is None:
        return SourceBlock(SourceBlockKind.LIST_ITEM, _segments(item, unit_of), ordered=bool(item.enumerated))
    text, parts = _inline_text(inline, document)
    used.update([*parts, inline.self_ref])
    return SourceBlock(SourceBlockKind.LIST_ITEM, ((text, unit_of(item)),), ordered=bool(item.enumerated))


def _heading(text: str, unit: int, level: int) -> SourceBlock:
    return SourceBlock(SourceBlockKind.HEADING, ((_clean(text), unit),), level=level)


def _table_block(item: Any, unit: int) -> SourceBlock | None:
    rows = _rows(item)
    return SourceBlock(SourceBlockKind.TABLE, ((" ", unit),), rows=rows) if rows else None


def _leaf_block(item: Any, document: Any, unit_of, heading_level, used: set[str]) -> SourceBlock | None:
    from docling_core.types.doc.document import (  # noqa: PLC0415
        CodeItem,
        ListItem,
        SectionHeaderItem,
        TableItem,
        TextItem,
        TitleItem,
    )

    unit = unit_of(item)
    block: SourceBlock | None = None
    match item:
        case TitleItem():
            block = _heading(item.text, unit, 1)
        case SectionHeaderItem():
            block = _heading(item.text, unit, heading_level(item))
        case TableItem():
            block = _table_block(item, unit)
        case ListItem():
            block = _list_block(item, document, unit_of, used)
        case CodeItem():
            block = SourceBlock(SourceBlockKind.CODE, ((sanitize_text(item.text or ""), unit),))
        case TextItem():
            block = SourceBlock(SourceBlockKind.PARAGRAPH, _segments(item, unit_of))
        case _:
            pass
    return block


def _item_block(item: Any, document: Any, unit_of, heading_level, used: set[str]) -> SourceBlock | None:
    from docling_core.types.doc.document import GroupItem, InlineGroup  # noqa: PLC0415

    if item.self_ref in used:
        return None
    if isinstance(item, InlineGroup):
        return _inline_block(item, document, unit_of, used)
    if isinstance(item, GroupItem):
        return None
    return _leaf_block(item, document, unit_of, heading_level, used)


def _item_blocks(document: Any, unit_of, heading_level) -> list[SourceBlock]:
    used: set[str] = set()
    made = [
        _item_block(item, document, unit_of, heading_level, used)
        for item, _depth in document.iterate_items(with_groups=True)
    ]
    return [block for block in made if block is not None]


def _first_heading(blocks: Sequence[SourceBlock]) -> str:
    return next((b.segments[0][0] for b in blocks if b.kind is SourceBlockKind.HEADING), "")


def _block_chars(block: SourceBlock) -> int:
    return sum(len(text) for text, _ in block.segments) + sum(map(_row_chars, block.rows))


def _row_chars(row: Sequence[str]) -> int:
    return sum(map(len, row))


def _total_chars(blocks: Sequence[SourceBlock]) -> int:
    return sum(map(_block_chars, blocks))


class _Reading:
    """Turn any error of a library on a bad file into a `ParseError`."""

    def __init__(self, kind: str) -> None:
        self._kind = kind

    def __enter__(self) -> None:
        return None

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None, trace: object) -> None:
        if error is None or isinstance(error, ParseError | ConfigError) or not isinstance(error, Exception):
            return
        if isinstance(error, MemoryError | RecursionError):
            raise limit_exceeded(f"{self._kind}_structure") from error
        # The type only. A message can hold text of the document.
        _LOG.warning("A %s file could not be read: %s", self._kind, type(error).__name__)
        raise ParseError(ParseReason.INVALID, f"The {self._kind} file could not be read.") from error


def _check_result(result: Any) -> None:
    from docling.datamodel.base_models import ConversionStatus  # noqa: PLC0415

    if result.status not in {ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS}:
        raise ParseError(ParseReason.INVALID, "The document could not be read.")


class _DeclarativeParser:
    """DOCX and HTML: headings, paragraphs, lists and tables, with the body order as the position."""

    _kind = ""
    _suffix = ""
    _version = ""

    def __init__(
        self,
        limits: DocumentLimits | None = None,
        lock: AbstractContextManager[object] | None = None,
        models: Path | None = None,
    ) -> None:
        """Make a parser. `lock` is the shared lock of binary parses."""
        self._limits = limits or DocumentLimits()
        self._lock = lock
        self._models = models

    @property
    def kind(self) -> str:
        """The source kind."""
        return self._kind

    @property
    def version(self) -> str:
        """The parser version, with the Docling version."""
        return f"{self._version}+docling{_docling_version()}"

    def _check(self, raw: RawDocument) -> None:
        check_size(raw.data, self._limits.max_binary_bytes)
        check_zip(raw.data, self._limits)

    def parse(self, raw: RawDocument, deadline: Deadline) -> ParsedDocument:
        """Parse the document."""
        self._check(raw)
        with parse_lock(self._lock):
            deadline.restart()
            deadline.check()
            with _Reading(self._kind):
                result = _convert(raw.data, f"document{self._suffix}", None, self._models)
            deadline.check()
        _check_result(result)
        counter = _Counter()
        blocks = _item_blocks(result.document, counter, lambda item: item.level + 1)
        if _total_chars(blocks) > self._limits.max_document_chars:
            raise limit_exceeded("max_document_chars")
        title = raw.title or _first_heading(blocks) or raw.doc_id
        form = render_markdown(blocks)
        sections = build_sections(form, title, _block_span)
        return ParsedDocument(source_document(raw, title), sections, ParseReport())


class _Counter:
    """Gives each item the next number of the body order."""

    def __init__(self) -> None:
        self._seen: dict[int, int] = {}

    def __call__(self, item: Any) -> int:
        return self._seen.setdefault(id(item), len(self._seen) + 1)


def _block_span(first: int, last: int) -> Span:
    return BlockSpan(first, last)


class DocxParser(_DeclarativeParser):
    """Word files. Hidden text, deleted text and headers and footers are left out by Docling."""

    _kind = "docx"
    _suffix = ".docx"
    _version = "docx-v3"


class HtmlParser(_DeclarativeParser):
    """HTML files. Navigation and page footers are left out by Docling."""

    _kind = "html"
    _suffix = ".html"
    _version = "html-v3"

    def _check(self, raw: RawDocument) -> None:
        check_size(raw.data, self._limits.max_html_bytes)
        # Each element starts with "<", so this cheap count is an upper bound that stops the parse early.
        if raw.data.count(b"<") > self._limits.max_html_elements:
            raise limit_exceeded("max_html_elements")


class XlsxParser(_DeclarativeParser):
    """Excel files: one section for each sheet, with the rows of the sheet as the position."""

    _kind = "xlsx"
    _suffix = ".xlsx"
    _version = "xlsx-v1"

    def parse(self, raw: RawDocument, deadline: Deadline) -> ParsedDocument:
        """Parse the document."""
        from docling_core.types.doc.document import TableItem  # noqa: PLC0415
        from docling_core.types.doc.labels import GroupLabel  # noqa: PLC0415

        self._check(raw)
        with parse_lock(self._lock):
            deadline.restart()
            deadline.check()
            with _Reading(self._kind):
                result = _convert(raw.data, "document.xlsx", None, self._models)
            deadline.check()
        _check_result(result)
        document = result.document
        names = [_clean(g.name) for g in document.groups if g.label == GroupLabel.SHEET]
        by_sheet: dict[int, list[Any]] = {}
        for item, _depth in document.iterate_items(with_groups=False):
            if isinstance(item, TableItem) and item.prov:
                by_sheet.setdefault(item.prov[0].page_no, []).append(item)
        title = raw.title or raw.doc_id
        sections: list[Section] = []
        total = 0
        for sheet, items in sorted(by_sheet.items()):
            sheet_sections, chars = _sheet(names, sheet, items, title)
            total += chars
            if total > self._limits.max_document_chars:  # Stop before the next sheet uses more memory.
                raise limit_exceeded("max_document_chars")
            sections += sheet_sections
        if not sections:
            raise ParseError(ParseReason.INVALID, "The Excel file has no table.")
        return ParsedDocument(source_document(raw, title), tuple(sections), ParseReport())


def _table_with_rows(item: Any) -> SourceBlock:
    rows = _rows(item)
    first_row = round(item.prov[0].bbox.t) + 1
    units = tuple(first_row + index for index in range(len(rows)))
    return SourceBlock(SourceBlockKind.TABLE, ((" ", first_row),), rows=rows, row_units=units)


def _sheet(names: Sequence[str], sheet: int, items: Sequence[Any], title: str) -> tuple[list[Section], int]:
    """Return the sections of one sheet, and the number of its characters."""
    name = names[sheet - 1] if sheet <= len(names) else f"Sheet {sheet}"
    blocks = [SourceBlock(SourceBlockKind.HEADING, ((name, 1),), level=1), *map(_table_with_rows, items)]

    def span(first: int, last: int) -> Span:
        return CellSpan(name, first, last)

    sections = [s for s in build_sections(render_markdown(blocks), title, span) if s.anchor != "h"]
    return sections, _total_chars(blocks)


def _pdf_unit(item: Any) -> int:
    return item.prov[0].page_no if item.prov else 1


def _pdf_heading_levels(document: Any) -> dict[int, int]:
    """Return a level for each heading height, because Docling gives all PDF headings the same level.

    A larger heading has a smaller level. Heights that differ by less than 1.5 points are one level. The title is
    level 1, so that section headings start at level 2.
    """
    from docling_core.types.doc.document import SectionHeaderItem  # noqa: PLC0415

    heights = sorted(
        {
            round(item.prov[0].bbox.height)
            for item, _ in document.iterate_items(with_groups=False)
            if isinstance(item, SectionHeaderItem) and item.prov
        },
        reverse=True,
    )
    levels: dict[int, int] = {}
    level, previous = 1, None
    for height in heights:
        if previous is not None and previous - height >= _HEADING_SIZE_STEP:
            level = min(level + 1, 6)
        levels[height] = level
        previous = height
    return levels


def _join_across_pages(blocks: list[SourceBlock]) -> list[SourceBlock]:
    """Join a paragraph that a page end cut into two items."""
    joined: list[SourceBlock] = []
    for block in blocks:
        if joined and _continues(joined[-1], block):
            before = joined.pop()
            joined.append(SourceBlock(SourceBlockKind.PARAGRAPH, before.segments + block.segments))
        else:
            joined.append(block)
    return joined


def _continues(before: SourceBlock, after: SourceBlock) -> bool:
    if before.kind is not SourceBlockKind.PARAGRAPH or after.kind is not SourceBlockKind.PARAGRAPH:
        return False
    text = before.segments[-1][0].rstrip()
    return bool(text) and not text.endswith(_SENTENCE_ENDS) and after.segments[0][1] == before.segments[-1][1] + 1


def _page_texts(block: SourceBlock) -> list[tuple[str, int]]:
    rows = [(" ".join(row), block.segments[0][1]) for row in block.rows]
    return [*block.segments, *rows]


def _non_space(text: str) -> int:
    return sum(1 for char in text if not char.isspace())


def _page_chars(blocks: Sequence[SourceBlock]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for text, page in chain.from_iterable(map(_page_texts, blocks)):
        counts[page] = counts.get(page, 0) + _non_space(text)
    return counts


class PdfParser(_DeclarativeParser):
    """PDF files. Sections follow headings and can start in the middle of a page. Pages are only source positions."""

    _kind = "pdf"
    _suffix = ".pdf"
    _version = "pdf-v2"

    def __init__(
        self,
        limits: DocumentLimits | None = None,
        lock: AbstractContextManager[object] | None = None,
        models: Path | None = None,
        *,
        seconds_per_page: float = PDF_SECONDS_PER_PAGE,
    ) -> None:
        """Make a parser. `lock` is the shared lock of binary parses. `models` is the folder of the layout models."""
        super().__init__(limits, lock, models)
        self._seconds_per_page = seconds_per_page

    @property
    def version(self) -> str:
        """The parser version, with the versions of Docling and of the layout models."""
        return f"pdf-v2+docling{_docling_version()}+layout{LAYOUT_REVISION}+table{TABLE_REVISION}"

    def _page_count(self, data: bytes) -> int:
        from docling.backend.docling_parse_backend import DoclingParseDocumentBackend  # noqa: PLC0415
        from docling.datamodel.base_models import InputFormat  # noqa: PLC0415
        from docling.datamodel.document import InputDocument  # noqa: PLC0415

        document = InputDocument(
            path_or_stream=BytesIO(data),
            format=InputFormat.PDF,
            backend=DoclingParseDocumentBackend,
            filename="document.pdf",
        )
        if not document.valid:
            # A file that an empty password opens is valid. Other encrypted files cannot be opened.
            if b"/Encrypt" in data:
                raise ParseError(ParseReason.ENCRYPTED, "The PDF file needs a password.")
            raise ParseError(ParseReason.INVALID, "The document is not a valid PDF file.")
        count = int(document.page_count)
        document._backend.unload()
        return count

    def parse(self, raw: RawDocument, deadline: Deadline) -> ParsedDocument:
        """Parse the document."""
        check_size(raw.data, self._limits.max_binary_bytes)
        if self._models is None or not self._models.is_dir():
            raise ConfigError("The PDF parser needs the folder of the layout models.")
        with parse_lock(self._lock):
            deadline.restart()
            deadline.check()
            with _Reading(self._kind):
                pages = self._page_count(raw.data)
            if pages > self._limits.max_pdf_pages:
                raise limit_exceeded("max_pdf_pages")
            seconds = max(deadline.seconds, pages * self._seconds_per_page)
            deadline.restart(seconds)
            started = time.monotonic()
            with _Reading(self._kind):
                timeout = None if math.isinf(seconds) else seconds
                result = _convert(raw.data, "document.pdf", timeout, self._models)
            deadline.check()
        _check_result(result)
        if self._timed_out(result, started, seconds):
            raise ParseError(ParseReason.PARSE_TIMEOUT, f"The document took longer than {seconds:g} s to parse.")
        return self._build(raw, result.document, pages)

    @staticmethod
    def _timed_out(result: Any, started: float, seconds: float) -> bool:
        from docling.datamodel.base_models import ConversionStatus  # noqa: PLC0415

        return result.status is ConversionStatus.PARTIAL_SUCCESS and time.monotonic() - started >= seconds * 0.95

    def _build(self, raw: RawDocument, document: Any, pages: int) -> ParsedDocument:
        levels = _pdf_heading_levels(document)

        def level_of(item: Any) -> int:
            return levels.get(round(item.prov[0].bbox.height), 1) + 1 if item.prov else 2

        blocks = _join_across_pages(_item_blocks(document, _pdf_unit, level_of))
        counts = _page_chars(blocks)
        skipped: list[SkippedPage] = []
        dropped: set[int] = set()
        for page in range(1, pages + 1):
            count = counts.get(page, 0)
            if count > self._limits.max_page_chars or count < self._limits.min_page_chars:
                reason = "page_text_limit" if count > self._limits.max_page_chars else "no_text"
                skipped.append(SkippedPage(page, str(page), reason))
                dropped.add(page)
        kept = [_without_pages(block, dropped) for block in blocks]
        kept = [b for b in kept if b.kind is SourceBlockKind.HEADING or b.segments]
        if not any(b.kind is not SourceBlockKind.HEADING for b in kept):
            raise ParseError(ParseReason.NO_TEXT_LAYER, "No page of the PDF file has a text layer. v1 has no OCR.")
        if _total_chars(kept) > self._limits.max_document_chars:
            raise limit_exceeded("max_document_chars")
        title = raw.title or _first_heading(kept) or raw.doc_id

        def span(first: int, last: int) -> Span:
            return PageSpan(first, last, str(first), str(last))

        form = render_markdown(kept)
        labelled = tuple(_with_labels(section) for section in build_sections(form, title, span))
        report = ParseReport(pages_total=pages, pages_indexed=pages - len(skipped), skipped_pages=tuple(skipped))
        return ParsedDocument(source_document(raw, title), labelled, report)


def _without_pages(block: SourceBlock, dropped: set[int]) -> SourceBlock:
    """Remove the text of skipped pages from a block. A heading stays, because it names the sections after it."""
    if block.kind is SourceBlockKind.HEADING:
        return block
    return replace(block, segments=tuple(part for part in block.segments if part[1] not in dropped))


def _with_labels(section: Section) -> Section:
    return replace(section, unit_labels={unit: str(unit) for _, unit in section.units})
