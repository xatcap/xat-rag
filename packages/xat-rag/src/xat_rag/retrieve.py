"""The retrieve use case: ranked chunks from the RAGs of a context, for one request."""

import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from xat_rag_core.errors import InternalError, InvalidRequestError, StageTimeoutError
from xat_rag_core.model import Candidate, Chunk, PageSpan
from xat_rag_core.query import build_query
from xat_rag_core.ranking import MAX_CHUNKS, final_score, merge_ranked

from xat_rag.index import RagIndex
from xat_rag.recipes import ContextRecipe
from xat_rag.types import ChunkSource, DebugCandidate, OmittedItem, RetrievalResult, RetrievedChunk


@dataclass(frozen=True, slots=True)
class ContextRequest:
    """A context request: a question and, if the recipe default does not fit, a number of chunks."""

    context: str
    question: str
    chunks: int | None = None
    debug: bool = False


def chunk_uri(chunk: Chunk) -> str:
    """Return the URI of a chunk. A PDF chunk URI ends with `#page=N`."""
    if isinstance(chunk.span, PageSpan):
        return f"{chunk.uri}#page={chunk.span.page_start}"
    return chunk.uri


def retrieved_chunks(candidates: Sequence[Candidate], index_of: Mapping[str, RagIndex]) -> tuple[RetrievedChunk, ...]:
    """Return the public form of candidates, in the given order. `index_of` maps a RAG name to its index.

    A concurrent delete can remove a document after the search. Its candidates are left out.
    """
    doc_ids: dict[str, set[str]] = defaultdict(set)
    for candidate in candidates:
        doc_ids[candidate.chunk.rag].add(candidate.chunk.doc_id)
    versions = {rag: index_of[rag].store.document_versions(ids) for rag, ids in doc_ids.items()}
    return tuple(
        _retrieved(candidate, versions[candidate.chunk.rag][candidate.chunk.doc_id])
        for candidate in candidates
        if candidate.chunk.doc_id in versions[candidate.chunk.rag]
    )


def _retrieved(candidate: Candidate, version: str) -> RetrievedChunk:
    chunk = candidate.chunk
    if chunk.token_count is None:
        raise InternalError("A stored chunk has no size.")
    return RetrievedChunk(
        chunk_id=chunk.chunk_id,
        parent_id=chunk.parent_id,
        kind=chunk.kind,
        score=final_score(candidate),
        size_tokens=chunk.token_count,
        header=chunk.header,
        text=chunk.text,
        source=ChunkSource(
            rag=chunk.rag,
            doc_id=chunk.doc_id,
            source_kind=chunk.source_kind.value,
            uri=chunk_uri(chunk),
            title=chunk.title,
            version=version,
            span=chunk.span,
        ),
        metadata=dict(chunk.metadata),
    )


def retrieve(request: ContextRequest, recipe: ContextRecipe, *, indexes: Mapping[str, RagIndex]) -> RetrievalResult:
    """Return the ranked chunks of one request. The app decides what to do with them."""
    limit = recipe.context.chunks if request.chunks is None else request.chunks
    if not 1 <= limit <= MAX_CHUNKS:
        raise InvalidRequestError(f"The number of chunks must be 1 to {MAX_CHUNKS}.")
    started = time.monotonic()
    query = build_query(request.question)
    if not query.dense_text:
        raise InvalidRequestError("A context request needs a question.")
    if (time.monotonic() - started) * 1000 > recipe.limits.query_build:
        raise StageTimeoutError("query_build", recipe.limits.query_build)

    by_rag: list[tuple[Candidate, ...]] = []
    debug: list[DebugCandidate] = []
    duplicates: list[OmittedItem] = []
    for source in recipe.context.sources:
        retrieved = indexes[source.rag].retrieve(query, recipe.limits)
        duplicates.extend(OmittedItem(chunk_id, "duplicate") for chunk_id in retrieved.duplicates)
        by_rag.append(retrieved.candidates)
        if request.debug:
            debug.extend(
                DebugCandidate(
                    candidate.chunk_id,
                    source.rag,
                    candidate.scores.lexical_rank,
                    candidate.scores.dense_rank,
                    candidate.scores.rrf_score,
                    candidate.scores.rerank_score,
                    candidate.scores.rerank_truncated,
                )
                for candidate in retrieved.candidates
            )

    started = time.monotonic()
    merged = merge_ranked(by_rag, limit)
    chunks = retrieved_chunks(merged.chosen, indexes)
    if (time.monotonic() - started) * 1000 > recipe.limits.pack:
        raise StageTimeoutError("pack", recipe.limits.pack)
    omitted = tuple(OmittedItem(chunk_id, reason.value) for chunk_id, reason in merged.omitted)
    return RetrievalResult(chunks, omitted + tuple(duplicates), tuple(debug) if request.debug else None)
