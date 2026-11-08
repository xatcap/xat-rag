import json
import math
from pathlib import Path

import pytest
from xat_rag_core.errors import ConfigError
from xat_rag_core.model import Candidate, Chunk, LineSpan, SourceKind, StageScores
from xat_rag_eval.harness import KindReport, Report, gate, load_golden, stage_order
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


def _report(recall: float, violations: list[str] | None = None, kind: str = "jira") -> Report:
    return Report({kind: KindReport(cases=1, recall={"reranked": recall}, pack_recall=1.0)}, violations or [], [], {})


BASELINE = {"kinds": {"jira": {"recall": {"reranked": 0.90}, "pack_recall": 1.0}}}


def test_gate_blocks_a_drop_of_more_than_two_points() -> None:
    assert gate(_report(0.885), BASELINE) == []
    assert gate(_report(0.88), BASELINE) == []  # Exactly 2 points is allowed.
    assert gate(_report(0.87), BASELINE) == ["the recall@10 of 'jira' dropped from 0.9000 to 0.8700"]
    assert gate(_report(1.0, ["c1"]), BASELINE) == ["budget rule failed for case c1"]


def test_gate_fails_closed() -> None:
    assert gate(_report(1.0), {}) == ["the baseline has no 'kinds' object"]
    assert gate(_report(1.0, kind="docs"), BASELINE) == [
        "the kind 'docs' is not in the baseline",
        "the kind 'jira' is missing from this run",
    ]
    broken = {"kinds": {"jira": {"recall": "x", "pack_recall": 1.0}}}
    assert gate(_report(1.0), broken) == ["the recall@10 of 'jira' is missing from the baseline or the run"]


def test_golden_set_validation(tmp_path: Path) -> None:
    good = {"id": "a", "context": "c", "rag": "r", "kind": "k", "question": "q", "relevant": [{"doc_id": "d"}]}
    cases = [
        {**good, "relevant": []},
        {**good, "relevant": [{"grade": 1}]},
        {**good, "relevant": [{"doc_id": "d", "chunk_id": "c"}]},
        {**good, "relevant": [{"doc_id": "d"}, {"chunk_id": "c"}]},
        {**good, "question": ""},
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
