"""`PdfParser` (`pdf-v1`): sections from the outline, or one section for each page.

Pages with no text layer are skipped and reported. v1 has no OCR. See ADR 0012.
"""

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from io import BytesIO
from typing import cast

import pypdf
from pypdf import Configuration, PdfReader, apply_configuration
from pypdf.errors import LimitReachedError, PyPdfError
from pypdf.generic import Destination
from xat_rag_core.errors import ParseError, ParseReason
from xat_rag_core.ids import heading_anchor
from xat_rag_core.model import PageSpan, ParsedDocument, ParseReport, RawDocument, Section, SkippedPage
from xat_rag_core.parsing import Deadline, source_document
from xat_rag_core.sanitize import sanitize_text

from xat_rag_documents.limits import DocumentLimits, check_size, limit_exceeded, parse_lock


def pypdf_limits(limit: int) -> Configuration:
    """Return the pypdf configuration for one parse: lower decompression limits, and no external decoder program.

    pypdf keeps its configuration in a context variable, so this changes only the current parse.
    """
    return Configuration(
        maximum_declared_stream_length=limit,
        array_based_stream_maximum_output_length=limit,
        jbig2_maximum_output_length=limit,
        lzw_maximum_output_length=limit,
        run_length_maximum_output_length=limit,
        zlib_maximum_output_length=limit,
        brotli_maximum_output_length=limit,
        image_maximum_buffer_size=limit,
        jbig2dec_binary=None,
        disable_legacy_handling=True,
    )


@dataclass(frozen=True, slots=True)
class _Page:
    number: int  # 1-based, absolute.
    label: str | None
    text: str


def _outline_entries(reader: PdfReader, deadline: Deadline) -> list[tuple[int, list[str]]]:
    """Return (1-based page, heading path) for each outline entry, in document order."""
    entries: list[tuple[int, list[str]]] = []

    def walk(items: Sequence[object], path: list[str], depth: int) -> None:
        if depth > 16:
            return
        last_title: str | None = None
        for item in items:
            deadline.check()
            if isinstance(item, list):
                walk(cast(list[object], item), [*path, last_title] if last_title else path, depth + 1)
            elif isinstance(item, Destination):
                last_title = " ".join(str(item.title or "").split())
                page = reader.get_destination_page_number(item)
                if page is not None and page >= 0 and last_title:
                    entries.append((page + 1, [*path, last_title]))

    try:
        walk(cast(list[object], reader.outline), [], 0)
    except (PyPdfError, ValueError, KeyError, TypeError, AttributeError, IndexError, RecursionError):
        return []
    return entries


class PdfParser:
    """PDF files with a text layer."""

    def __init__(
        self, limits: DocumentLimits | None = None, lock: AbstractContextManager[object] | None = None
    ) -> None:
        """Make a parser. `lock` is the shared lock of binary parses."""
        self._limits = limits or DocumentLimits()
        self._lock = lock

    @property
    def kind(self) -> str:
        """The source kind."""
        return "pdf"

    @property
    def version(self) -> str:
        """The parser version, with the pypdf version."""
        return f"pdf-v1+pypdf{pypdf.__version__}"

    def parse(self, raw: RawDocument, deadline: Deadline) -> ParsedDocument:
        """Parse the document."""
        check_size(raw.data, self._limits.max_binary_bytes)
        with parse_lock(self._lock), apply_configuration(pypdf_limits(self._limits.max_decompressed_stream)):
            deadline.restart()
            try:
                return self._parse(raw, deadline)
            except ParseError:
                raise
            except LimitReachedError as error:
                raise limit_exceeded("pypdf_limit") from error
            except (MemoryError, RecursionError) as error:
                raise limit_exceeded("pdf_structure") from error
            except Exception as error:  # A hostile PDF can make pypdf raise any type.
                raise ParseError(ParseReason.INVALID, "The PDF file could not be read.") from error

    def _parse(self, raw: RawDocument, deadline: Deadline) -> ParsedDocument:
        try:
            reader = PdfReader(BytesIO(raw.data), strict=False)
            encrypted = reader.is_encrypted
        except (PyPdfError, ValueError, KeyError, TypeError, OSError) as error:
            raise ParseError(ParseReason.INVALID, "The document is not a valid PDF file.") from error
        if encrypted:
            raise ParseError(ParseReason.ENCRYPTED, "The PDF file is encrypted. xat-rag v1 refuses encrypted files.")
        try:
            page_count = len(reader.pages)
        except (PyPdfError, ValueError, KeyError, TypeError) as error:
            raise ParseError(ParseReason.INVALID, "The page tree of the PDF file is not valid.") from error
        if page_count > self._limits.max_pdf_pages:
            raise limit_exceeded("max_pdf_pages")
        try:
            labels: list[str] = list(reader.page_labels)
        except (PyPdfError, ValueError, KeyError, TypeError, IndexError):
            labels = []

        pages, skipped = self._pages(reader, page_count, labels, deadline)
        if not pages:
            raise ParseError(ParseReason.NO_TEXT_LAYER, "No page of the PDF file has a text layer. v1 has no OCR.")
        title = raw.title or self._metadata_title(reader) or raw.doc_id
        entries = _outline_entries(reader, deadline)
        sections = self._outline_sections(pages, entries, title) if entries else self._page_sections(pages, title)
        report = ParseReport(pages_total=page_count, pages_indexed=len(pages), skipped_pages=tuple(skipped))
        return ParsedDocument(source_document(raw, title), tuple(sections), report)

    def _pages(
        self, reader: PdfReader, page_count: int, labels: list[str], deadline: Deadline
    ) -> tuple[list[_Page], list[SkippedPage]]:
        pages: list[_Page] = []
        skipped: list[SkippedPage] = []
        total = 0
        for index in range(page_count):
            deadline.check()
            number = index + 1
            label = " ".join(sanitize_text(labels[index]).split())[:32] if index < len(labels) else None
            try:
                text = sanitize_text(reader.pages[index].extract_text() or "")
            except (LimitReachedError, MemoryError, RecursionError):
                raise  # A passed limit stops the whole document.
            except Exception:  # pypdf raises many types on a bad page. The page is skipped and reported.
                skipped.append(SkippedPage(number, label, "extract_error"))
                continue
            if len(text) > self._limits.max_page_chars:
                skipped.append(SkippedPage(number, label, "page_text_limit"))
                continue
            if sum(1 for char in text if not char.isspace()) < self._limits.min_page_chars:
                skipped.append(SkippedPage(number, label, "no_text"))
                continue
            total += len(text)
            if total > self._limits.max_document_chars:
                raise limit_exceeded("max_document_chars")
            pages.append(_Page(number, label, text.strip("\n")))
        return pages, skipped

    @staticmethod
    def _metadata_title(reader: PdfReader) -> str:
        try:
            metadata = reader.metadata
            return " ".join(str(metadata.title).split()) if metadata is not None and metadata.title else ""
        except (PyPdfError, ValueError, KeyError, TypeError):
            return ""

    @staticmethod
    def _section(anchor: str, header: str, pages: list[_Page]) -> Section:
        units: list[tuple[int, int]] = []
        parts: list[str] = []
        offset = 0
        for page in pages:
            units.append((offset, page.number))
            parts.append(page.text)
            offset += len(page.text) + 2
        first, last = pages[0], pages[-1]
        return Section(
            anchor=anchor,
            kind="section" if anchor.startswith("h") else "page",
            header=header,
            text="\n\n".join(parts),
            span=PageSpan(first.number, last.number, first.label, last.label),
            units=tuple(units),
            unit_labels={page.number: page.label for page in pages if page.label},
        )

    @staticmethod
    def _empty(anchor: str, header: str, page: int) -> Section:
        return Section(anchor=anchor, kind="section", header=header, text="", span=PageSpan(page, page))

    def _page_sections(self, pages: list[_Page], title: str) -> list[Section]:
        return [
            self._section(f"p/{page.number}", f"{title} > page {page.label or page.number}", [page]) for page in pages
        ]

    def _outline_sections(self, pages: list[_Page], entries: list[tuple[int, list[str]]], title: str) -> list[Section]:
        starts: dict[int, list[str]] = {}
        for page, path in entries:
            starts.setdefault(page, path)  # The first heading on a page wins.
        ordered = sorted(starts.items())
        sections: list[Section] = []
        before = [page for page in pages if page.number < ordered[0][0]]
        if before:
            sections.append(self._section("h", title, before))
        for position, (start, path) in enumerate(ordered):
            end = ordered[position + 1][0] if position + 1 < len(ordered) else 1 << 30
            group = [page for page in pages if start <= page.number < end]
            anchor, header = heading_anchor(path), " > ".join([title, *path])
            # An outline entry with no text page stays as an empty section, so that the IDs of the
            # later sections do not change when it gets text.
            sections.append(self._section(anchor, header, group) if group else self._empty(anchor, header, start))
        return sections
