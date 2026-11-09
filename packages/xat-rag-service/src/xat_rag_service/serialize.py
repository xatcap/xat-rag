"""JSON forms of the result types."""

from dataclasses import asdict
from typing import Any

from xat_rag.types import DocumentResult, RetrievalResult, RetrievedChunk
from xat_rag_core.model import BlockSpan, CellSpan, LineSpan, PageSpan, Span
from xat_rag_core.query import RetrievalQuery


def span_json(span: Span) -> dict[str, Any]:
    """Return a span with its type and named fields."""
    match span:
        case LineSpan():
            return {"type": "line", "line_start": span.line_start, "line_end": span.line_end}
        case PageSpan():
            return {
                "type": "page",
                "page_start": span.page_start,
                "page_end": span.page_end,
                "label_start": span.label_start,
                "label_end": span.label_end,
            }
        case BlockSpan():
            return {"type": "block", "block_start": span.block_start, "block_end": span.block_end}
        case CellSpan():
            return {"type": "cell", "sheet": span.sheet, "row_start": span.row_start, "row_end": span.row_end}


def document_result_json(result: DocumentResult) -> dict[str, Any]:
    """Return the result of one document."""
    data = asdict(result)
    data["skipped_pages"] = [asdict(page) for page in result.skipped_pages]
    data["notes"] = list(result.notes)
    return data


def chunk_json(chunk: RetrievedChunk) -> dict[str, Any]:
    """Return one retrieved chunk."""
    source = chunk.source
    return {
        "chunk_id": chunk.chunk_id,
        "parent_id": chunk.parent_id,
        "kind": chunk.kind,
        "score": chunk.score,
        "size_tokens": chunk.size_tokens,
        "header": chunk.header,
        "text": chunk.text,
        "source": {
            "rag": source.rag,
            "doc_id": source.doc_id,
            "source_kind": source.source_kind,
            "uri": source.uri,
            "title": source.title,
            "version": source.version,
            "span": span_json(source.span),
        },
        "metadata": dict(chunk.metadata),
    }


def result_json(result: RetrievalResult) -> dict[str, Any]:
    """Return the result of a context request."""
    return {
        "chunks": [chunk_json(chunk) for chunk in result.chunks],
        "omitted": [asdict(item) for item in result.omitted],
        "debug": None if result.debug is None else [asdict(item) for item in result.debug],
    }


def query_json(query: RetrievalQuery) -> dict[str, Any]:
    """Return a retrieval query."""
    return {
        "lexical_terms": list(query.lexical_terms),
        "dense_text": query.dense_text,
        "rerank_text": query.rerank_text,
    }
