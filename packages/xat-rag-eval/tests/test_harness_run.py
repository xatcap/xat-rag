"""Tests of the harness `run`: final recall, final MRR, source coverage, invariants and determinism."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from xat_rag import ContextRequest, RetrievalResult, RetrievedChunk, XatRag
from xat_rag.recipes import ContextRecipe
from xat_rag_core.model import Candidate, Chunk, LineSpan, SourceKind, StageScores
from xat_rag_eval.harness import FINAL_NS, Case, Relevant, gate, report_json, run


def chunk(chunk_id: str, doc_id: str, rag: str = "docs", score: float = 1.0) -> RetrievedChunk:
    from xat_rag import ChunkSource  # noqa: PLC0415

    source = ChunkSource(rag, doc_id, "markdown", "", "", "", LineSpan(1, 1))
    return RetrievedChunk(chunk_id, None, "section", score, 1, "h", "t", source, {})


def ranked(*ids: str, rag: str = "docs", doc: dict[str, str] | None = None) -> list[RetrievedChunk]:
    """Return chunks with falling scores. `doc` maps a chunk ID to its document ID (default: the same ID)."""
    return [chunk(i, (doc or {}).get(i, i), rag, score=1.0 - n / 100) for n, i in enumerate(ids)]


class FakeRecipe:
    def __init__(self, rags: Sequence[str]) -> None:
        self.context = type("Section", (), {"sources": [type("Source", (), {"rag": r})() for r in rags]})()


@dataclass
class FakeRag:
    """A stand-in for `XatRag` that returns fixed chunks."""

    chunks: list[RetrievedChunk]
    rags: Sequence[str] = ("docs",)
    candidates: list[Candidate] = field(default_factory=list[Candidate])
    requests: list[ContextRequest] = field(default_factory=list[ContextRequest])
    calls: int = 0
    vary: bool = False

    def context_recipe(self, context: str) -> ContextRecipe:
        return cast(ContextRecipe, FakeRecipe(self.rags))

    def search(self, rag: str, query: object, context: str | None = None) -> Any:
        return type("Retrieved", (), {"candidates": tuple(self.candidates)})()

    def retrieve(self, request: ContextRequest) -> RetrievalResult:
        self.requests.append(request)
        self.calls += 1
        if self.vary and self.calls % 2 == 0:
            return RetrievalResult(tuple(reversed(self.chunks)))
        return RetrievalResult(tuple(self.chunks))


def case(relevant: list[Relevant], case_id: str = "c1", kind: str = "md", rag: str = "docs") -> Case:
    return Case(id=case_id, context="ask", rag=rag, kind=kind, question="the question", relevant=relevant)


def run_with(fake: FakeRag, *cases: Case) -> Any:
    return run(cast(XatRag, fake), list(cases))


def test_the_final_recall_is_measured_at_3_5_and_10() -> None:
    assert FINAL_NS == (3, 5, 10)
    ids = [f"c{n}" for n in range(1, 11)]
    fake = FakeRag(ranked(*ids))
    report = run_with(fake, case([Relevant(chunk_id="c4")]))
    item = report.kinds["md"]
    assert item.final_recall == {"3": 0.0, "5": 1.0, "10": 1.0}
    assert item.final_mrr == 0.25


def test_a_relevant_chunk_at_rank_1_has_full_recall_everywhere_and_mrr_one() -> None:
    fake = FakeRag(ranked("a", "b", "c", "d", "e"))
    item = run_with(fake, case([Relevant(chunk_id="a")])).kinds["md"]
    assert item.final_recall == {"3": 1.0, "5": 1.0, "10": 1.0}
    assert item.final_mrr == 1.0


def test_a_relevant_chunk_at_rank_6_is_found_at_10_only() -> None:
    fake = FakeRag(ranked(*[f"c{n}" for n in range(1, 11)]))
    item = run_with(fake, case([Relevant(chunk_id="c6")])).kinds["md"]
    assert item.final_recall == {"3": 0.0, "5": 0.0, "10": 1.0}
    assert item.final_mrr == pytest.approx(1 / 6, abs=1e-4)


def test_recall_counts_the_share_of_the_relevant_chunks_found() -> None:
    fake = FakeRag(ranked("a", "x", "b", "y", "z", "c"))
    relevant = [Relevant(chunk_id="a"), Relevant(chunk_id="b"), Relevant(chunk_id="c"), Relevant(chunk_id="d")]
    item = run_with(fake, case(relevant)).kinds["md"]
    assert item.final_recall["3"] == 0.5
    assert item.final_recall["5"] == 0.5
    assert item.final_recall["10"] == 0.75


def test_a_missing_relevant_chunk_gives_zero() -> None:
    item = run_with(FakeRag(ranked("a", "b")), case([Relevant(chunk_id="zzz")])).kinds["md"]
    assert item.final_recall == {"3": 0.0, "5": 0.0, "10": 0.0}
    assert item.final_mrr == 0.0


def test_the_request_asks_for_10_chunks_with_the_question() -> None:
    fake = FakeRag(ranked("a"))
    run_with(fake, case([Relevant(chunk_id="a")]))
    assert fake.requests[0] == ContextRequest("ask", "the question", chunks=10)


def test_a_document_case_counts_chunks_for_n_and_documents_for_relevance() -> None:
    chunks = ranked("d1#a", "d1#b", "d1#c", "d2#a", doc={"d1#a": "d1", "d1#b": "d1", "d1#c": "d1", "d2#a": "d2"})
    item = run_with(FakeRag(chunks), case([Relevant(doc_id="d2")])).kinds["md"]
    assert item.final_recall == {"3": 0.0, "5": 1.0, "10": 1.0}
    assert item.final_mrr == 0.5  # Repeated documents count once in the order of the documents.


def test_the_source_coverage_is_the_share_of_cases_with_a_chunk_of_the_case_rag() -> None:
    fake = FakeRag(ranked("a", "b", rag="faq"), rags=("docs", "faq"))
    hit = case([Relevant(chunk_id="a")], "c1", rag="faq")
    miss = case([Relevant(chunk_id="a")], "c2", rag="docs")
    assert run_with(fake, hit).kinds["md"].source_coverage == 1.0
    assert run_with(fake, miss).kinds["md"].source_coverage == 0.0
    assert run_with(fake, hit, miss).kinds["md"].source_coverage == 0.5


def test_a_case_with_a_rag_that_is_not_in_its_context_is_a_config_error() -> None:
    from xat_rag_core.errors import ConfigError  # noqa: PLC0415

    with pytest.raises(ConfigError, match="not in its context"):
        run_with(FakeRag(ranked("a")), case([Relevant(chunk_id="a")], rag="other"))


def test_the_metrics_are_averaged_per_kind() -> None:
    fake = FakeRag(ranked("a", "b", "c", "d"))
    one = case([Relevant(chunk_id="a")], "c1", kind="jira")
    two = case([Relevant(chunk_id="d")], "c2", kind="jira")
    other = case([Relevant(chunk_id="zz")], "c3", kind="diff")
    report = run_with(fake, one, two, other)
    assert report.kinds["jira"].cases == 2
    assert report.kinds["jira"].final_mrr == pytest.approx((1 + 0.25) / 2)
    assert report.kinds["jira"].final_recall["3"] == 0.5
    assert report.kinds["diff"].final_mrr == 0.0


def test_a_good_run_has_no_violations_and_no_changes() -> None:
    report = run_with(FakeRag(ranked("a", "b", "c")), case([Relevant(chunk_id="a")]))
    assert report.invariant_violations == []
    assert report.nondeterministic == []
    assert gate(report, {"kinds": {"md": _baseline(report)}}) == []


def _baseline(report: Any) -> dict[str, Any]:
    return report_json(report)["kinds"]["md"]


def test_a_repeated_chunk_id_is_an_invariant_violation() -> None:
    fake = FakeRag(ranked("a", "b") + ranked("a"))
    report = run_with(fake, case([Relevant(chunk_id="a")]))
    assert report.invariant_violations == ["c1"]
    assert gate(report, {"kinds": {"md": _baseline(report)}}) == ["an invariant failed for case c1"]


def test_scores_that_rise_are_an_invariant_violation() -> None:
    fake = FakeRag([chunk("a", "a", score=0.1), chunk("b", "b", score=0.9)])
    assert run_with(fake, case([Relevant(chunk_id="a")])).invariant_violations == ["c1"]


def test_equal_scores_are_allowed() -> None:
    fake = FakeRag([chunk("a", "a", score=0.5), chunk("b", "b", score=0.5)])
    assert run_with(fake, case([Relevant(chunk_id="a")])).invariant_violations == []


def test_more_chunks_than_the_limit_are_an_invariant_violation() -> None:
    fake = FakeRag(ranked(*[f"c{n:02d}" for n in range(11)]))
    assert run_with(fake, case([Relevant(chunk_id="c00")])).invariant_violations == ["c1"]


def test_a_result_that_changes_between_two_runs_is_reported() -> None:
    fake = FakeRag(ranked("a", "b", "c"), vary=True)
    report = run_with(fake, case([Relevant(chunk_id="a")]))
    assert report.nondeterministic == ["c1"]
    assert gate(report, {"kinds": {"md": _baseline(report)}}) == ["the result of case c1 changed between two runs"]


def test_the_report_json_has_the_final_metrics_and_not_the_old_ones() -> None:
    fake = FakeRag(ranked("a", "b"))
    fake.candidates = [
        Candidate(
            Chunk("a", "d", "docs", SourceKind.TEXT, "s", "h", "t", LineSpan(1, 1), "x", "v"),
            StageScores(lexical_rank=1, rrf_score=0.5),
        )
    ]
    data = report_json(run_with(fake, case([Relevant(chunk_id="a")])))
    assert set(data) == {"kinds", "invariant_violations", "nondeterministic", "timing_ms"}
    kind = data["kinds"]["md"]
    assert {"final_recall", "final_mrr", "source_coverage", "recall", "ndcg", "mrr", "cases"} <= set(kind)
    assert "pack_recall" not in kind
    assert set(kind["recall"]) == {"lexical", "dense", "fused", "reranked"}
    assert kind["recall"]["lexical"] == 1.0
    assert {"retrieve_context_p50", "retrieve_context_p95"} <= set(data["timing_ms"])
    assert not any(name.startswith("compose") for name in data["timing_ms"])


def test_an_empty_final_list_gives_zero_and_no_violation() -> None:
    report = run_with(FakeRag([]), case([Relevant(chunk_id="a")]))
    assert report.kinds["md"].final_recall == {"3": 0.0, "5": 0.0, "10": 0.0}
    assert report.kinds["md"].final_mrr == 0.0
    assert report.invariant_violations == []
