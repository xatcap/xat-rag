"""The public result types of the xat-rag library. They hold only xat-rag and standard types."""

from collections.abc import Mapping
from dataclasses import dataclass

from xat_rag_core.model import Scalar, SkippedPage, Span, StageScores


@dataclass(frozen=True, slots=True)
class DocumentResult:
    """The result of the ingestion of one document. It never holds document text."""

    doc_id: str
    status: str
    chunks: int = 0
    embedded: int = 0
    error_type: str | None = None
    reason: str | None = None
    message: str | None = None
    pages_total: int | None = None
    pages_indexed: int | None = None
    skipped_pages: tuple[SkippedPage, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChunkSource:
    """Where a chunk came from, and where to open it."""

    rag: str
    doc_id: str
    source_kind: str
    uri: str
    title: str
    version: str
    span: Span


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """One retrieved chunk. `size_tokens` comes from the size ruler of xat-rag, not from the LLM of the app."""

    chunk_id: str
    parent_id: str | None
    kind: str
    score: float
    size_tokens: int
    header: str
    text: str
    source: ChunkSource
    metadata: Mapping[str, Scalar]


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One result of a search in one RAG, with the scores of each stage."""

    chunk: RetrievedChunk
    scores: StageScores


@dataclass(frozen=True, slots=True)
class OmittedItem:
    """A candidate that is not in the result, and why: `duplicate`, `low_score` or `limit`."""

    chunk_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class DebugCandidate:
    """The scores of one candidate at each stage. Only when the request asks for debug output."""

    chunk_id: str
    rag: str
    lexical_rank: int | None
    dense_rank: int | None
    rrf_score: float | None
    rerank_score: float | None
    rerank_truncated: bool


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """The result of a context request. `chunks` is in rank order, best first."""

    chunks: tuple[RetrievedChunk, ...]
    omitted: tuple[OmittedItem, ...] = ()
    debug: tuple[DebugCandidate, ...] | None = None
