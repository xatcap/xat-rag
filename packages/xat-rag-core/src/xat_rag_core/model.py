"""The domain model: documents, chunks, spans and candidates."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

type Scalar = str | int | float | bool
"""A metadata value."""


def _empty_metadata() -> dict[str, Scalar]:
    return {}


class SourceKind(StrEnum):
    """The kind of a source document."""

    MARKDOWN = "markdown"
    TEXT = "text"
    DIFF = "diff"
    JIRA = "jira"
    PROMPT = "prompt"
    PDF = "pdf"
    DOCX = "docx"
    HTML = "html"


@dataclass(frozen=True, slots=True)
class LineSpan:
    """Lines of the source text. For HTML, lines of the HTML source."""

    line_start: int
    line_end: int


@dataclass(frozen=True, slots=True)
class PageSpan:
    """Absolute pages of a PDF, with the page labels for display."""

    page_start: int
    page_end: int
    label_start: str | None = None
    label_end: str | None = None


@dataclass(frozen=True, slots=True)
class BlockSpan:
    """Body blocks (paragraphs or tables) of a DOCX document."""

    block_start: int
    block_end: int


type Span = LineSpan | PageSpan | BlockSpan


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """One document that a caller sent to a RAG."""

    doc_id: str
    rag: str
    source_kind: SourceKind
    uri: str
    title: str
    version: str
    content_hash: str
    metadata: Mapping[str, Scalar] = field(default_factory=_empty_metadata)


@dataclass(frozen=True, slots=True)
class Chunk:
    """One searchable part of a document."""

    chunk_id: str
    doc_id: str
    rag: str
    source_kind: SourceKind
    kind: str
    header: str
    text: str
    span: Span
    content_hash: str
    chunker_version: str
    uri: str = ""
    title: str = ""
    parent_id: str | None = None
    token_count: int | None = None
    metadata: Mapping[str, Scalar] = field(default_factory=_empty_metadata)


@dataclass(frozen=True, slots=True)
class StageScores:
    """The rank or score of a candidate at each stage. `None` means that the stage did not find it."""

    lexical_rank: int | None = None
    dense_rank: int | None = None
    exact_rank: int | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None
    rerank_truncated: bool = False


@dataclass(frozen=True, slots=True)
class Candidate:
    """A chunk that a retrieval stage found, with its scores."""

    chunk: Chunk
    scores: StageScores = field(default_factory=StageScores)

    @property
    def chunk_id(self) -> str:
        """The ID of the chunk."""
        return self.chunk.chunk_id


@dataclass(frozen=True, slots=True)
class RawDocument:
    """One document as the caller sends it: the bytes and the document fields."""

    doc_id: str
    rag: str
    source_kind: SourceKind
    data: bytes
    uri: str = ""
    title: str = ""
    version: str = ""
    media_type: str = ""
    metadata: Mapping[str, Scalar] = field(default_factory=_empty_metadata)


def _no_units() -> tuple[tuple[int, int], ...]:
    return ()


def _no_labels() -> Mapping[int, str]:
    return {}


@dataclass(frozen=True, slots=True)
class Section:
    """A structural part of a parsed document, before chunking.

    `units` maps character offsets in `text` to page or block numbers, so that a piece of a
    split section gets its own span. For a `LineSpan`, lines are counted from the text.
    """

    anchor: str
    kind: str
    header: str
    text: str
    span: Span
    parent_anchor: str | None = None
    splittable: bool = True
    units: tuple[tuple[int, int], ...] = field(default_factory=_no_units)
    unit_labels: Mapping[int, str] = field(default_factory=_no_labels)
    metadata: Mapping[str, Scalar] = field(default_factory=_empty_metadata)


@dataclass(frozen=True, slots=True)
class SkippedPage:
    """A PDF page that was not indexed, and the reason (`no_text`, `extract_error` or `page_text_limit`)."""

    page: int
    label: str | None
    reason: str


def _no_skipped() -> tuple[SkippedPage, ...]:
    return ()


def _no_notes() -> tuple[str, ...]:
    return ()


@dataclass(frozen=True, slots=True)
class ParseReport:
    """Facts about a parse that go into the result of the document. Never document text."""

    pages_total: int | None = None
    pages_indexed: int | None = None
    skipped_pages: tuple[SkippedPage, ...] = field(default_factory=_no_skipped)
    notes: tuple[str, ...] = field(default_factory=_no_notes)


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """The result of a parser: the document, its sections, and the report."""

    source: SourceDocument
    sections: tuple[Section, ...]
    report: ParseReport = field(default_factory=ParseReport)
