"""Conversion between xat-rag types and LangChain `Document`.

This module is the only code that reads or writes `Document.metadata`. The retrievers and the
reranker use its functions, so the untyped dictionary does not spread. See ADR 0001.
"""

import copy
from typing import Any, NotRequired, TypedDict, cast

from langchain_core.documents import Document

from xat_rag_core.errors import InternalError
from xat_rag_core.model import (
    BlockSpan,
    Candidate,
    Chunk,
    LineSpan,
    PageSpan,
    Scalar,
    SourceKind,
    Span,
    StageScores,
)


class SpanMeta(TypedDict):
    """The span of a chunk, as metadata. `start` and `end` are lines, pages or blocks."""

    type: str
    start: int
    end: int
    label_start: NotRequired[str | None]
    label_end: NotRequired[str | None]


class ChunkMeta(TypedDict):
    """All metadata keys that xat-rag writes into a LangChain `Document`."""

    chunk_id: str
    doc_id: str
    rag: str
    source_kind: str
    kind: str
    header: str
    uri: str
    title: str
    parent_id: str | None
    content_hash: str
    chunker_version: str
    token_count: int | None
    span: SpanMeta
    source: dict[str, Scalar]
    lexical_rank: NotRequired[int | None]
    dense_rank: NotRequired[int | None]
    exact_rank: NotRequired[int | None]
    rrf_score: NotRequired[float | None]
    rerank_score: NotRequired[float | None]
    rerank_truncated: NotRequired[bool]


def span_to_meta(span: Span) -> SpanMeta:
    """Convert a span to metadata."""
    match span:
        case LineSpan(line_start=start, line_end=end):
            return {"type": "line", "start": start, "end": end}
        case PageSpan(page_start=start, page_end=end, label_start=label_start, label_end=label_end):
            return {"type": "page", "start": start, "end": end, "label_start": label_start, "label_end": label_end}
        case BlockSpan(block_start=start, block_end=end):
            return {"type": "block", "start": start, "end": end}


def span_from_meta(meta: SpanMeta) -> Span:
    """Convert metadata to a span. Raise `InternalError` for a bad value."""
    if not isinstance(meta, dict):  # pyright: ignore[reportUnnecessaryIsInstance] - metadata is untyped at runtime.
        raise InternalError("A chunk span in the metadata is not an object.")
    kind = meta.get("type")
    try:
        if kind == "line":
            return LineSpan(meta["start"], meta["end"])
        if kind == "page":
            return PageSpan(meta["start"], meta["end"], meta.get("label_start"), meta.get("label_end"))
        if kind == "block":
            return BlockSpan(meta["start"], meta["end"])
    except KeyError as error:
        raise InternalError("A chunk span in the metadata is not complete.") from error
    raise InternalError("A chunk span in the metadata has an unknown type.")


def chunk_to_document(chunk: Chunk, scores: StageScores | None = None) -> Document:
    """Convert a chunk to a LangChain `Document`. `Document.id` is the chunk ID."""
    meta: ChunkMeta = {
        "chunk_id": chunk.chunk_id,
        "doc_id": chunk.doc_id,
        "rag": chunk.rag,
        "source_kind": chunk.source_kind.value,
        "kind": chunk.kind,
        "header": chunk.header,
        "uri": chunk.uri,
        "title": chunk.title,
        "parent_id": chunk.parent_id,
        "content_hash": chunk.content_hash,
        "chunker_version": chunk.chunker_version,
        "token_count": chunk.token_count,
        "span": span_to_meta(chunk.span),
        "source": dict(chunk.metadata),
    }
    if scores is not None:
        meta["lexical_rank"] = scores.lexical_rank
        meta["dense_rank"] = scores.dense_rank
        meta["exact_rank"] = scores.exact_rank
        meta["rrf_score"] = scores.rrf_score
        meta["rerank_score"] = scores.rerank_score
        meta["rerank_truncated"] = scores.rerank_truncated
    return Document(page_content=chunk.text, metadata=cast(dict[str, Any], meta), id=chunk.chunk_id)


def candidate_to_document(candidate: Candidate) -> Document:
    """Convert a candidate to a LangChain `Document`, with its scores."""
    return chunk_to_document(candidate.chunk, candidate.scores)


def _meta(document: Document) -> ChunkMeta:
    return cast(ChunkMeta, document.metadata)


def document_to_chunk(document: Document) -> Chunk:
    """Convert a LangChain `Document` that xat-rag made back to a chunk."""
    meta = _meta(document)
    try:
        return Chunk(
            chunk_id=meta["chunk_id"],
            doc_id=meta["doc_id"],
            rag=meta["rag"],
            source_kind=SourceKind(meta["source_kind"]),
            kind=meta["kind"],
            header=meta["header"],
            text=document.page_content,
            span=span_from_meta(meta["span"]),
            content_hash=meta["content_hash"],
            chunker_version=meta["chunker_version"],
            uri=meta.get("uri", ""),
            title=meta.get("title", ""),
            parent_id=meta.get("parent_id"),
            token_count=meta.get("token_count"),
            metadata=dict(meta.get("source", {})),
        )
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise InternalError("A LangChain document does not have the xat-rag chunk metadata.") from error


def document_to_candidate(document: Document) -> Candidate:
    """Convert a LangChain `Document` that xat-rag made back to a candidate, with its scores."""
    meta = _meta(document)
    scores = StageScores(
        lexical_rank=meta.get("lexical_rank"),
        dense_rank=meta.get("dense_rank"),
        exact_rank=meta.get("exact_rank"),
        rrf_score=meta.get("rrf_score"),
        rerank_score=meta.get("rerank_score"),
        rerank_truncated=meta.get("rerank_truncated", False),
    )
    return Candidate(chunk=document_to_chunk(document), scores=scores)


def chunk_id_of(document: Document) -> str:
    """Return the chunk ID of a document that xat-rag made. `Document.id` and the metadata must agree."""
    meta_id = _meta(document).get("chunk_id")
    if document.id and meta_id and document.id != meta_id:
        raise InternalError("The ID of a LangChain document does not match its chunk ID.")
    value = document.id or meta_id
    if not value:
        raise InternalError("A LangChain document has no chunk ID.")
    return value


def with_rerank_score(document: Document, score: float, truncated: bool) -> Document:
    """Return a copy of `document` with its rerank score."""
    meta = copy.deepcopy(dict(_meta(document)))
    meta["rerank_score"] = score
    meta["rerank_truncated"] = truncated
    return Document(page_content=document.page_content, metadata=meta, id=document.id)


def with_rank(document: Document, stage: str, rank: int) -> Document:
    """Return a copy of `document` with its rank at `stage` (`lexical`, `dense` or `exact`)."""
    key = {"lexical": "lexical_rank", "dense": "dense_rank", "exact": "exact_rank"}.get(stage)
    if key is None:
        raise InternalError("The stage of a rank is unknown.")
    meta = copy.deepcopy(dict(_meta(document)))
    meta[key] = rank
    return Document(page_content=document.page_content, metadata=meta, id=document.id)
