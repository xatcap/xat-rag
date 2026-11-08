"""The public result types of the xat-rag library. They hold no LangChain types (ADR 0001)."""

from dataclasses import dataclass, field
from typing import Any

from xat_rag_core.model import SkippedPage, Span
from xat_rag_core.render import ChatMessage


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
class PackedChunk:
    """One chunk in a context pack, with its label."""

    label: str
    chunk_id: str
    doc_id: str
    rag: str
    kind: str
    uri: str
    title: str
    score: float
    span: Span
    tokens: int
    text: str


@dataclass(frozen=True, slots=True)
class PinnedItem:
    """A pinned prompt in a context pack."""

    prompt_id: str
    tokens: int
    text: str


@dataclass(frozen=True, slots=True)
class InputReport:
    """The input section of a context pack."""

    text: str
    tokens: int
    dropped_templates: int = 0
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class BudgetReport:
    """How the window is used. `chunks` is the number of tokens that the packed chunks use."""

    window: int
    reserved_output: int
    overhead: int
    pinned: int
    input: int
    chunks: int
    margin: int
    unused: int
    floors_lowered: bool


@dataclass(frozen=True, slots=True)
class OmittedItem:
    """A candidate that is not in the pack, and why: `budget`, `quota` or `duplicate`."""

    chunk_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class DebugCandidate:
    """The scores of one candidate at each stage. Only when the request asks for debug output."""

    chunk_id: str
    rag: str
    lexical_rank: int | None
    dense_rank: int | None
    exact_rank: int | None
    rrf_score: float | None
    rerank_score: float | None
    rerank_truncated: bool


@dataclass(frozen=True, slots=True)
class ContextPack:
    """The result of a context request. The app sends `messages` and `grammar` to the generator."""

    pack_id: str
    generator: str
    max_tokens: int
    budget: BudgetReport
    pinned: tuple[PinnedItem, ...]
    chunks: tuple[PackedChunk, ...]
    input: InputReport
    messages: tuple[ChatMessage, ...]
    grammar: str
    json_schema: dict[str, Any] = field(default_factory=dict[str, Any])
    omitted: tuple[OmittedItem, ...] = ()
    debug: tuple[DebugCandidate, ...] | None = None
