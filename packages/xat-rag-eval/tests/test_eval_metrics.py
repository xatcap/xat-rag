import json
import math
from pathlib import Path

import pytest
from xat_rag_core.errors import ConfigError
from xat_rag_core.model import Candidate, Chunk, LineSpan, SourceKind, StageScores
from xat_rag_eval.harness import (
    MAX_MRR_DROP_BP,
    MAX_RECALL_DROP_BP,
    STAGES,
    KindReport,
    Report,
    gate,
    load_golden,
    stage_order,
)
from xat_rag_eval.metrics import ndcg_at, percentile, recall_at, reciprocal_rank


def test_recall_ndcg_mrr() -> None:
    relevant = {"a": 2, "b": 1}
    assert recall_at(["x", "a", "y"], relevant, 2) == 0.5
    assert recall_at([], {}, 10) == 1.0
    assert reciprocal_rank(["x", "y", "b"], relevant) == pytest.approx(1 / 3)
    assert reciprocal_rank(["x"], relevant) == 0.0
    assert ndcg_at(["a", "b"], relevant, 10) == pytest.approx(1.0)
    worse = ndcg_at(["b", "a"], relevant, 10)
    assert worse < 1.0
    assert worse == pytest.approx((1 + 3 / math.log2(3)) / (3 + 1 / math.log2(3)))


def test_percentile() -> None:
    assert percentile([5, 1, 3, 2, 4], 0.5) == 3
    assert percentile([5, 1, 3, 2, 4], 0.95) == 5
    assert percentile([], 0.5) == 0.0


def _report(
    recall: float,
    violations: list[str] | None = None,
    kind: str = "jira",
    final_recall: dict[str, float] | None = None,
    final_mrr: float = 0.8,
    nondeterministic: list[str] | None = None,
) -> Report:
    item = KindReport(
        cases=1,
        recall={"reranked": recall},
        final_recall={"3": 0.8, "5": 0.9, "10": 1.0} if final_recall is None else final_recall,
        final_mrr=final_mrr,
        source_coverage=1.0,
    )
    return Report({kind: item}, violations or [], nondeterministic or [], {})


BASELINE = {
    "kinds": {"jira": {"recall": {"reranked": 0.90}, "final_recall": {"3": 0.8, "5": 0.9, "10": 1.0}, "final_mrr": 0.8}}
}


def test_gate_blocks_a_drop_of_more_than_two_points() -> None:
    assert gate(_report(0.885), BASELINE) == []
    assert gate(_report(0.88), BASELINE) == []  # Exactly 2 points is allowed.
    assert gate(_report(0.87), BASELINE) == ["the recall@10 of 'jira' dropped from 0.9000 to 0.8700"]
    assert gate(_report(1.0, ["c1"]), BASELINE) == ["an invariant failed for case c1"]
    assert gate(_report(1.0, nondeterministic=["c2"]), BASELINE) == ["the result of case c2 changed between two runs"]


def test_gate_blocks_a_drop_of_the_final_recall_at_5_and_at_10() -> None:
    assert gate(_report(0.9, final_recall={"3": 0.8, "5": 0.88, "10": 0.98}), BASELINE) == []
    assert gate(_report(0.9, final_recall={"3": 0.8, "5": 0.87, "10": 1.0}), BASELINE) == [
        "the final recall@5 of 'jira' dropped from 0.9000 to 0.8700"
    ]
    assert gate(_report(0.9, final_recall={"3": 0.8, "5": 0.9, "10": 0.97}), BASELINE) == [
        "the final recall@10 of 'jira' dropped from 1.0000 to 0.9700"
    ]
    both = gate(_report(0.9, final_recall={"3": 0.8, "5": 0.5, "10": 0.5}), BASELINE)
    assert [problem.split(" of ")[0] for problem in both] == ["the final recall@5", "the final recall@10"]


def test_the_final_recall_at_3_does_not_fail_the_gate() -> None:
    assert gate(_report(0.9, final_recall={"3": 0.0, "5": 0.9, "10": 1.0}), BASELINE) == []


def test_gate_allows_a_drop_of_the_final_mrr_of_up_to_ten_points() -> None:
    assert MAX_MRR_DROP_BP == 1_000
    assert gate(_report(0.9, final_mrr=0.75), BASELINE) == []
    assert gate(_report(0.9, final_mrr=0.70), BASELINE) == []  # Exactly 10 points is allowed.
    assert gate(_report(0.9, final_mrr=0.6999), BASELINE) == ["the final MRR of 'jira' dropped from 0.8000 to 0.6999"]
    assert gate(_report(0.9, final_mrr=0.0), BASELINE) == ["the final MRR of 'jira' dropped from 0.8000 to 0.0000"]
    assert gate(_report(0.9, final_mrr=1.0), BASELINE) == []


def test_the_recall_limit_is_two_points_and_the_mrr_limit_is_ten() -> None:
    assert MAX_RECALL_DROP_BP == 200
    assert MAX_MRR_DROP_BP == 5 * MAX_RECALL_DROP_BP


def test_gate_fails_closed() -> None:
    assert gate(_report(1.0), {}) == ["the baseline has no 'kinds' object"]
    assert gate(_report(1.0, kind="docs"), BASELINE) == [
        "the kind 'docs' is not in the baseline",
        "the kind 'jira' is missing from this run",
    ]
    broken = {"kinds": {"jira": {**BASELINE["kinds"]["jira"], "recall": "x"}}}
    assert gate(_report(1.0), broken) == ["the recall@10 of 'jira' is missing from the baseline or the run"]


@pytest.mark.parametrize(("missing", "name"), [("final_mrr", "final MRR"), ("final_recall", "final recall@5")])
def test_gate_fails_when_the_baseline_has_no_final_metric(missing: str, name: str) -> None:
    entry = {key: value for key, value in BASELINE["kinds"]["jira"].items() if key != missing}
    problems = gate(_report(0.9), {"kinds": {"jira": entry}})
    assert f"the {name} of 'jira' is missing from the baseline or the run" in problems


def test_gate_fails_for_an_old_baseline_with_pack_recall_only() -> None:
    old = {"kinds": {"jira": {"recall": {"reranked": 0.9}, "pack_recall": 1.0}}}
    problems = gate(_report(0.9), old)
    assert "the final MRR of 'jira' is missing from the baseline or the run" in problems
    assert "the final recall@10 of 'jira' is missing from the baseline or the run" in problems


def test_gate_fails_when_this_run_has_no_final_recall() -> None:
    problems = gate(_report(0.9, final_recall={}), BASELINE)
    assert problems == [
        "the final recall@5 of 'jira' is missing from the baseline or the run",
        "the final recall@10 of 'jira' is missing from the baseline or the run",
    ]


def test_golden_set_validation(tmp_path: Path) -> None:
    good = {"id": "a", "context": "c", "rag": "r", "kind": "k", "question": "q", "relevant": [{"doc_id": "d"}]}
    cases = [
        {**good, "relevant": []},
        {**good, "relevant": [{"grade": 1}]},
        {**good, "relevant": [{"doc_id": "d", "chunk_id": "c"}]},
        {**good, "relevant": [{"doc_id": "d"}, {"chunk_id": "c"}]},
        {**good, "question": ""},
        {**good, "question": "   "},
        {**good, "question": "", "log_lines": ["ERROR x"]},
        {**good, "log_lines": ["ERROR x"]},
        {**good, "unknown": 1},
        {key: value for key, value in good.items() if key != "question"},
    ]
    for case in cases:
        path = tmp_path / "g.jsonl"
        path.write_text(json.dumps(case) + "\n")
        with pytest.raises(ConfigError):
            load_golden(path)
    path = tmp_path / "g.jsonl"
    path.write_text(json.dumps(good) + "\n" + json.dumps(good) + "\n")
    with pytest.raises(ConfigError, match="same ID"):
        load_golden(path)
    path.write_text("")
    with pytest.raises(ConfigError, match="empty"):
        load_golden(path)
    path.write_text(json.dumps(good) + "\n")
    assert load_golden(path)[0].by_document


def _candidate(chunk_id: str, lexical: int | None, rrf: float) -> Candidate:
    chunk = Chunk(chunk_id, "d", "r", SourceKind.TEXT, "s", "h", "t", LineSpan(1, 1), "h", "v")
    return Candidate(chunk, StageScores(lexical_rank=lexical, rrf_score=rrf))


def test_stage_order() -> None:
    candidates = [_candidate("b", 2, 0.1), _candidate("a", None, 0.3), _candidate("c", 1, 0.2)]
    assert [c.chunk_id for c in stage_order(candidates, "lexical")] == ["c", "b"]
    assert [c.chunk_id for c in stage_order(candidates, "fused")] == ["a", "c", "b"]
    assert [c.chunk_id for c in stage_order(candidates, "reranked")] == ["b", "a", "c"]


def test_the_stages_have_no_exact_stage() -> None:
    assert STAGES == ("lexical", "dense", "fused", "reranked")
