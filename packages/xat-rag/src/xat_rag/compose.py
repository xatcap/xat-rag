"""The compose use case: a context pack for one request. See the architecture, sections 6 to 8."""

import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from xat_rag_core.budget import GeneratorProfile, compute_budget
from xat_rag_core.citations import citation_grammar, citation_json_schema
from xat_rag_core.errors import InvalidRequestError, StageTimeoutError
from xat_rag_core.model import Candidate, Chunk, PageSpan
from xat_rag_core.packer import PackItem, SourceQuota, pack
from xat_rag_core.query import RetrievalQuery, TextQueryBuilder
from xat_rag_core.render import (
    chunk_label,
    render_chunk_block,
    render_input_block,
    render_messages,
    renderer_overhead_tokens,
)
from xat_rag_logs import LogInput, LogQueryBuilder, fit_input
from xat_rag_tokenizers import HfTokenCounter

from xat_rag.index import RagIndex
from xat_rag.prompts import PromptStore
from xat_rag.recipes import ContextRecipe
from xat_rag.types import (
    BudgetReport,
    ContextPack,
    DebugCandidate,
    InputReport,
    OmittedItem,
    PackedChunk,
    PinnedItem,
)

_LABEL_FOR_COUNT = "C9999"  # The longest label. Counting with it gives an upper bound.


@dataclass(frozen=True, slots=True)
class ContextRequest:
    """A context request. Values that the recipe sets can only go down, never up."""

    context: str
    question: str = ""
    log_lines: tuple[str, ...] = ()
    selected_lines: frozenset[int] = frozenset()
    reserved_output: int | None = None
    input_max_tokens: int | None = None
    debug: bool = False


def _lower(recipe_value: int, requested: int | None, name: str) -> int:
    if requested is None:
        return recipe_value
    if requested < 1 or requested > recipe_value:
        raise InvalidRequestError(f"'{name}' must be between 1 and {recipe_value}, the value of the recipe.")
    return requested


def chunk_uri(chunk: Chunk) -> str:
    """Return the URI of a chunk. For a PDF page span, it ends with `#page=N`, the standard PDF open parameter."""
    if isinstance(chunk.span, PageSpan) and chunk.uri:
        return f"{chunk.uri}#page={chunk.span.page_start}"
    return chunk.uri


def _uri(candidate: Candidate) -> str:
    return chunk_uri(candidate.chunk)


def _input_section(
    recipe: ContextRecipe, request: ContextRequest, cap: int, counter: HfTokenCounter
) -> tuple[RetrievalQuery, InputReport]:
    started = time.monotonic()
    if recipe.context.query_builder == "log-v1":
        analysis = LogQueryBuilder().analyze(LogInput(request.log_lines, request.selected_lines, request.question))
        fitted = fit_input(analysis, cap, lambda text: counter.count(render_input_block(text)))
        query, report = (
            analysis.query,
            InputReport(fitted.text, fitted.tokens, fitted.dropped_templates, fitted.truncated),
        )
    else:
        if request.log_lines:
            raise InvalidRequestError(f"The context '{recipe.context.name}' takes a question, not log lines.")
        if not request.question.strip():
            raise InvalidRequestError("A context request needs a question or log lines.")
        query = TextQueryBuilder().build(request.question)
        text = f"Question: {query.dense_text}"
        tokens = counter.count(render_input_block(text))
        if tokens > cap:
            raise InvalidRequestError(
                "The question is larger than the input cap of the context. Use a shorter question."
            )
        report = InputReport(text, tokens)
    if (time.monotonic() - started) * 1000 > recipe.limits.query_build:
        raise StageTimeoutError("query_build", recipe.limits.query_build)
    return query, report


def compose(
    request: ContextRequest,
    recipe: ContextRecipe,
    *,
    indexes: Mapping[str, RagIndex],
    prompts: PromptStore,
    counter: HfTokenCounter,
    generator: GeneratorProfile,
) -> ContextPack:
    """Make the context pack of one request. xat-rag never calls the generator (ADR 0008)."""
    section = recipe.context
    reserved_output = _lower(section.reserved_output, request.reserved_output, "reserved_output")
    input_cap = _lower(section.input_max_tokens, request.input_max_tokens, "input_max_tokens")
    query, input_report = _input_section(recipe, request, input_cap, counter)

    pinned = tuple(
        PinnedItem(prompt.prompt_id, counter.count(prompt.text), prompt.text)
        for prompt in (prompts.get(prompt_id) for prompt_id in section.pinned)
    )
    max_chunks = sum(indexes[source.rag].recipe.retrieval.keep for source in section.sources)
    budget = compute_budget(
        generator,
        window=section.window,
        reserved_output=reserved_output,
        pinned_tokens=sum(item.tokens for item in pinned),
        input_tokens=input_report.tokens,
        message_count=2,
        renderer_tokens=renderer_overhead_tokens(counter.count, len(pinned), max_chunks),
    )

    items_by_rag: dict[str, list[PackItem]] = {}
    debug: list[DebugCandidate] = []
    duplicates: list[OmittedItem] = []
    for source in section.sources:
        retrieved = indexes[source.rag].retrieve(query, recipe.limits)
        duplicates.extend(OmittedItem(chunk_id, "duplicate") for chunk_id in retrieved.duplicates)
        items: list[PackItem] = []
        for candidate in retrieved.candidates:
            scores = candidate.scores
            score = scores.rerank_score if scores.rerank_score is not None else (scores.rrf_score or 0.0)
            tokens = counter.count(render_chunk_block(_LABEL_FOR_COUNT, candidate.chunk))
            items.append(PackItem(candidate, score, tokens))
            if request.debug:
                debug.append(
                    DebugCandidate(
                        candidate.chunk_id,
                        source.rag,
                        scores.lexical_rank,
                        scores.dense_rank,
                        scores.exact_rank,
                        scores.rrf_score,
                        scores.rerank_score,
                        scores.rerank_truncated,
                    )
                )
        items_by_rag[source.rag] = items

    started = time.monotonic()
    quotas = [SourceQuota(source.rag, source.min_tokens, source.max_share) for source in section.sources]
    result = pack(items_by_rag, quotas, budget.chunks)
    chunks = tuple(
        PackedChunk(
            label=chunk_label(position),
            chunk_id=item.candidate.chunk_id,
            doc_id=item.candidate.chunk.doc_id,
            rag=item.candidate.chunk.rag,
            kind=item.candidate.chunk.source_kind.value,
            uri=_uri(item.candidate),
            title=item.candidate.chunk.title,
            score=item.score,
            span=item.candidate.chunk.span,
            tokens=item.tokens,
            text=item.candidate.chunk.text,
        )
        for position, item in enumerate(result.items, start=1)
    )
    blocks = [
        render_chunk_block(chunk.label, item.candidate.chunk) for chunk, item in zip(chunks, result.items, strict=True)
    ]
    messages = tuple(render_messages([item.text for item in pinned], blocks, render_input_block(input_report.text)))
    labels = [chunk.label for chunk in chunks]
    if (time.monotonic() - started) * 1000 > recipe.limits.pack:
        raise StageTimeoutError("pack", recipe.limits.pack)

    report = BudgetReport(
        window=budget.window,
        reserved_output=budget.reserved_output,
        overhead=budget.overhead,
        pinned=budget.pinned,
        input=budget.input,
        chunks=result.used_tokens,
        margin=budget.margin,
        unused=budget.chunks - result.used_tokens,
        floors_lowered=result.floors_lowered,
    )
    omitted = tuple(OmittedItem(item.chunk_id, item.reason.value) for item in result.omitted) + tuple(duplicates)
    return ContextPack(
        pack_id=f"p_{uuid.uuid4().hex[:20]}",
        generator=generator.name,
        max_tokens=reserved_output,
        budget=report,
        pinned=pinned,
        chunks=chunks,
        input=input_report,
        messages=messages,
        grammar=citation_grammar(labels),
        json_schema=citation_json_schema(labels),
        omitted=omitted,
        debug=tuple(debug) if request.debug else None,
    )
