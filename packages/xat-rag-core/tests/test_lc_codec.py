import pytest
from conftest import make_chunk
from langchain_core.documents import Document
from xat_rag_core import lc_codec
from xat_rag_core.errors import InternalError
from xat_rag_core.model import BlockSpan, Candidate, PageSpan, StageScores


def test_round_trip_keeps_all_fields() -> None:
    chunk = make_chunk("docs:a.md#h/x", text="body")
    document = lc_codec.chunk_to_document(chunk)
    assert document.id == "docs:a.md#h/x"
    assert document.page_content == "body"
    assert lc_codec.document_to_chunk(document) == chunk


def test_spans_round_trip() -> None:
    for span in (PageSpan(3, 4, "iii", "iv"), BlockSpan(2, 5)):
        assert lc_codec.span_from_meta(lc_codec.span_to_meta(span)) == span


def test_scores_are_written_on_copies() -> None:
    candidate = Candidate(make_chunk("a#b"), StageScores(lexical_rank=1, rrf_score=0.03))
    document = lc_codec.candidate_to_document(candidate)
    reranked = lc_codec.with_rerank_score(document, 0.8, truncated=True)
    ranked = lc_codec.with_rank(reranked, "dense", 4)
    back = lc_codec.document_to_candidate(ranked)
    assert back.scores == StageScores(
        lexical_rank=1, dense_rank=4, rrf_score=0.03, rerank_score=0.8, rerank_truncated=True
    )
    assert lc_codec.document_to_candidate(document).scores.rerank_score is None
    assert lc_codec.chunk_id_of(ranked) == "a#b"


def test_chunk_id_mismatch_is_an_error() -> None:

    document = lc_codec.chunk_to_document(make_chunk("a#b"))
    bad = Document(page_content=document.page_content, metadata=document.metadata, id="other")
    with pytest.raises(InternalError):
        lc_codec.chunk_id_of(bad)
