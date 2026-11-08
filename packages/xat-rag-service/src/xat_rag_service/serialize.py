"""JSON forms of the result types. The HTTP API has no LangChain types."""

from dataclasses import asdict
from typing import Any

from xat_rag.types import ContextPack, DocumentResult
from xat_rag_core.citations import CitationCheck
from xat_rag_core.model import BlockSpan, LineSpan, PageSpan, Span
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


def document_result_json(result: DocumentResult) -> dict[str, Any]:
    """Return the result of one document."""
    data = asdict(result)
    data["skipped_pages"] = [asdict(page) for page in result.skipped_pages]
    data["notes"] = list(result.notes)
    return data


def pack_json(pack: ContextPack) -> dict[str, Any]:
    """Return a context pack."""
    return {
        "pack_id": pack.pack_id,
        "generator": pack.generator,
        "max_tokens": pack.max_tokens,
        "budget": asdict(pack.budget),
        "pinned": [{"id": item.prompt_id, "tokens": item.tokens, "text": item.text} for item in pack.pinned],
        "chunks": [{**asdict(chunk), "span": span_json(chunk.span)} for chunk in pack.chunks],
        "input": asdict(pack.input),
        "rendered": {"messages": [{"role": m.role, "content": m.content} for m in pack.messages]},
        "citation": {"grammar": pack.grammar, "json_schema": pack.json_schema},
        "omitted": [asdict(item) for item in pack.omitted],
        "debug": None if pack.debug is None else [asdict(item) for item in pack.debug],
    }


def query_json(query: RetrievalQuery) -> dict[str, Any]:
    """Return a retrieval query."""
    return {
        "lexical_terms": list(query.lexical_terms),
        "dense_text": query.dense_text,
        "rerank_text": query.rerank_text,
        "exact_lookups": [{"field": lookup.field, "value": lookup.value} for lookup in query.exact_lookups],
    }


def citation_json(check: CitationCheck, uris: dict[str, str]) -> dict[str, Any]:
    """Return the result of a citation check, with the URI of each cited chunk."""
    return {
        "valid": check.valid,
        "problem": check.problem.value if check.problem is not None else None,
        "answer": check.answer,
        "citations": [
            {"label": c.label, "chunk_id": c.chunk_id, "uri": uris.get(c.chunk_id, "")} for c in check.citations
        ],
        "unknown_labels": list(check.unknown_labels),
        "missing_chunks": list(check.missing_chunks),
    }
