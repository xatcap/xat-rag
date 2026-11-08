from dataclasses import replace
from typing import Any, cast

import pytest
from conftest import make_chunk
from langchain_core.documents import Document
from xat_rag_core import lc_codec
from xat_rag_core.errors import InternalError
from xat_rag_core.model import BlockSpan, Candidate, LineSpan, PageSpan, SourceKind, StageScores


def _meta(document: Document) -> dict[str, Any]:
    return cast(dict[str, Any], document.metadata)


def _good() -> Document:
    return lc_codec.chunk_to_document(make_chunk("docs:a#1", text="body"))


@pytest.mark.parametrize(
    "key",
    ["chunk_id", "doc_id", "rag", "source_kind", "kind", "header", "span", "content_hash", "chunker_version"],
)
def test_missing_required_key_is_an_internal_error(key: str) -> None:
    document = _good()
    del _meta(document)[key]
    with pytest.raises(InternalError):
        lc_codec.document_to_chunk(document)
    with pytest.raises(InternalError):
        lc_codec.document_to_candidate(document)


def test_empty_metadata_is_an_internal_error() -> None:
    with pytest.raises(InternalError):
        lc_codec.document_to_chunk(Document(page_content="x", metadata={}))


def test_unknown_source_kind_is_an_internal_error() -> None:
    document = _good()
    _meta(document)["source_kind"] = "spreadsheet"
    with pytest.raises(InternalError):
        lc_codec.document_to_chunk(document)


@pytest.mark.parametrize(
    "span",
    [
        {"type": "sphere", "start": 1, "end": 2},
        {"start": 1, "end": 2},
        {"type": "line", "start": 1},
        {"type": "line", "end": 2},
        {"type": "page", "start": 1},
        {"type": "block", "end": 2},
        {},
    ],
)
def test_bad_span_metadata_is_an_internal_error(span: dict[str, object]) -> None:
    document = _good()
    _meta(document)["span"] = span
    with pytest.raises(InternalError):
        lc_codec.document_to_chunk(document)


@pytest.mark.parametrize("span", [None, "line", 5, ["line", 1, 2]])
def test_span_that_is_not_a_dictionary_is_an_internal_error(span: object) -> None:
    document = _good()
    _meta(document)["span"] = span
    with pytest.raises(InternalError):
        lc_codec.document_to_chunk(document)


def test_unknown_span_type_message_is_specific() -> None:
    with pytest.raises(InternalError, match="unknown type"):
        lc_codec.span_from_meta({"type": "x", "start": 1, "end": 2})
    with pytest.raises(InternalError, match="not complete"):
        lc_codec.span_from_meta({"type": "line", "start": 1})  # pyright: ignore[reportArgumentType]


def test_optional_keys_have_defaults() -> None:
    document = _good()
    for key in ("uri", "title", "parent_id", "token_count", "source"):
        del _meta(document)[key]
    chunk = lc_codec.document_to_chunk(document)
    assert (chunk.uri, chunk.title, chunk.parent_id, chunk.token_count) == ("", "", None, None)
    assert dict(chunk.metadata) == {}


def test_page_content_is_the_chunk_text_not_metadata() -> None:
    document = _good()
    assert lc_codec.document_to_chunk(Document(page_content="other", metadata=document.metadata)).text == "other"


def test_page_span_without_labels_round_trips() -> None:
    assert lc_codec.span_from_meta(lc_codec.span_to_meta(PageSpan(1, 2))) == PageSpan(1, 2, None, None)


def test_spans_of_each_type_have_the_type_name() -> None:
    assert lc_codec.span_to_meta(LineSpan(1, 2))["type"] == "line"
    assert lc_codec.span_to_meta(PageSpan(1, 2))["type"] == "page"
    assert lc_codec.span_to_meta(BlockSpan(1, 2))["type"] == "block"


def test_all_source_kinds_and_source_metadata_round_trip() -> None:
    for kind in SourceKind:
        chunk = make_chunk("a#b", kind=kind)
        document = lc_codec.chunk_to_document(chunk)
        assert lc_codec.document_to_chunk(document).source_kind is kind
    with_meta = replace(make_chunk("a#b"), metadata={"k": 1, "s": "v", "b": True})
    assert lc_codec.document_to_chunk(lc_codec.chunk_to_document(with_meta)) == with_meta


def test_chunk_id_of_prefers_the_document_id_and_falls_back_to_metadata() -> None:
    assert lc_codec.chunk_id_of(Document(page_content="x", metadata={}, id="from-id")) == "from-id"
    with pytest.raises(InternalError):
        lc_codec.chunk_id_of(Document(page_content="x", metadata={"chunk_id": "from-meta"}, id="from-id"))
    assert lc_codec.chunk_id_of(Document(page_content="x", metadata={"chunk_id": "from-meta"})) == "from-meta"
    assert lc_codec.chunk_id_of(Document(page_content="x", metadata={"chunk_id": "m"}, id="")) == "m"


def test_chunk_id_of_without_any_id_is_an_internal_error() -> None:
    with pytest.raises(InternalError):
        lc_codec.chunk_id_of(Document(page_content="x", metadata={}))
    with pytest.raises(InternalError):
        lc_codec.chunk_id_of(Document(page_content="x", metadata={"chunk_id": ""}, id=""))


def test_with_rank_rejects_an_unknown_stage_and_does_not_change_the_original() -> None:
    document = _good()
    with pytest.raises(InternalError):
        lc_codec.with_rank(document, "rerank", 1)
    ranked = lc_codec.with_rank(document, "exact", 3)
    assert "exact_rank" not in document.metadata
    assert _meta(ranked)["exact_rank"] == 3
    assert ranked.id == document.id
    assert lc_codec.with_rank(document, "lexical", 1).metadata["lexical_rank"] == 1


def test_with_rerank_score_does_not_change_the_original() -> None:
    document = _good()
    scored = lc_codec.with_rerank_score(document, 0.5, truncated=False)
    assert "rerank_score" not in document.metadata
    assert _meta(scored)["rerank_score"] == 0.5
    assert _meta(scored)["rerank_truncated"] is False


def test_candidate_without_score_keys_has_default_scores() -> None:
    candidate = lc_codec.document_to_candidate(_good())
    assert candidate.scores == StageScores()


def test_candidate_round_trip_keeps_a_zero_rank_and_a_zero_score() -> None:
    candidate = Candidate(make_chunk("a#b"), StageScores(lexical_rank=0, rrf_score=0.0, rerank_score=0.0))
    back = lc_codec.document_to_candidate(lc_codec.candidate_to_document(candidate))
    assert back.scores.lexical_rank == 0
    assert back.scores.rrf_score == 0.0
    assert back.scores.rerank_score == 0.0
    assert back.scores.dense_rank is None
