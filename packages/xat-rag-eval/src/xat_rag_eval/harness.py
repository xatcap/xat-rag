"""Run a golden set against an opened `XatRag`, and compare the result with a baseline.

Each case names the RAG whose stages it measures. Stage metrics use the candidates of that RAG
only, because ranks and RRF scores are per RAG. The `reranked` stage is the order that the packer
gets (after rerank, duplicate removal and MMR). A case scores either chunks or documents: for
documents, each ranked list is turned into a list of document IDs, first place first.
"""

import json
import statistics
import time
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator
from xat_rag import ContextRequest, XatRag
from xat_rag_core.errors import ConfigError
from xat_rag_core.model import Candidate
from xat_rag_core.query import RetrievalQuery, TextQueryBuilder
from xat_rag_logs import LogInput, LogQueryBuilder

from xat_rag_eval.metrics import ndcg_at, percentile, recall_at, reciprocal_rank

K = 10
MAX_RECALL_DROP_BP = 200  # 2 points, in basis points, so that the comparison has no float error.
STAGES = ("lexical", "dense", "exact", "fused", "reranked")


class Relevant(BaseModel):
    """One relevant item: a chunk ID or a document ID (exactly one), with a grade from 1 to 3."""

    model_config = ConfigDict(extra="forbid")
    chunk_id: str | None = None
    doc_id: str | None = None
    grade: int = Field(default=1, ge=1, le=3)

    @model_validator(mode="after")
    def _one_id(self) -> "Relevant":
        if (self.chunk_id is None) == (self.doc_id is None):
            raise ValueError("Give exactly one of chunk_id and doc_id.")
        return self


class Case(BaseModel):
    """One golden case."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    context: str = Field(min_length=1)
    rag: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    question: str = ""
    log_lines: list[str] = Field(default_factory=list[str])
    relevant: list[Relevant] = Field(min_length=1)

    @model_validator(mode="after")
    def _same_level(self) -> "Case":
        levels = {item.doc_id is not None for item in self.relevant}
        if len(levels) > 1:
            raise ValueError("A case scores chunks or documents, not both.")
        if not self.question.strip() and not self.log_lines:
            raise ValueError("A case needs a question or log lines.")
        return self

    @property
    def by_document(self) -> bool:
        """`True` if the case scores documents."""
        return self.relevant[0].doc_id is not None

    def relevant_ids(self) -> dict[str, int]:
        """Return the relevant IDs with their grades."""
        return {(item.doc_id or item.chunk_id or ""): item.grade for item in self.relevant}


def load_golden(path: Path) -> list[Case]:
    """Read a golden set in JSONL. Raise `ConfigError` for a bad case, a repeated ID or an empty set."""
    cases: list[Case] = []
    for number, line in enumerate(path.read_text("utf-8").splitlines(), start=1):
        if line.strip():
            try:
                cases.append(Case.model_validate_json(line))
            except ValueError as error:
                raise ConfigError(f"Line {number} of the golden set is not a valid case.") from error
    ids = [case.id for case in cases]
    if not cases or len(set(ids)) != len(ids):
        raise ConfigError("The golden set is empty, or two cases have the same ID.")
    return cases


@dataclass(slots=True)
class KindReport:
    """The metrics of one source kind."""

    cases: int = 0
    recall: dict[str, float] = field(default_factory=dict[str, float])
    ndcg: dict[str, float] = field(default_factory=dict[str, float])
    mrr: dict[str, float] = field(default_factory=dict[str, float])
    pack_recall: float = 0.0


@dataclass(slots=True)
class Report:
    """The result of a run. `timing_ms` is not part of the baseline comparison."""

    kinds: dict[str, KindReport]
    budget_violations: list[str]
    nondeterministic: list[str]
    timing_ms: dict[str, float]


def _ids(candidates: Sequence[Candidate], by_document: bool) -> list[str]:
    ids = [c.chunk.doc_id if by_document else c.chunk_id for c in candidates]
    return list(dict.fromkeys(ids))


def _rank_of(stage: str) -> Callable[[Candidate], int | None]:
    def rank(candidate: Candidate) -> int | None:
        scores = candidate.scores
        return {"lexical": scores.lexical_rank, "dense": scores.dense_rank, "exact": scores.exact_rank}[stage]

    return rank


def stage_order(candidates: Sequence[Candidate], stage: str) -> list[Candidate]:
    """Return the candidates of one RAG in the order of a stage. `reranked` keeps the given order."""
    if stage in {"lexical", "dense", "exact"}:
        rank = _rank_of(stage)
        found = [c for c in candidates if rank(c) is not None]
        return sorted(found, key=lambda c: (rank(c) or 0, c.chunk_id))
    if stage == "fused":
        return sorted(candidates, key=lambda c: (-(c.scores.rrf_score or 0.0), c.chunk_id))
    return list(candidates)


def _query(case: Case, builder: str) -> RetrievalQuery:
    if builder == "log-v1":
        return LogQueryBuilder().build(LogInput(tuple(case.log_lines), question=case.question))
    return TextQueryBuilder().build(case.question)


def run(rag: XatRag, cases: Sequence[Case]) -> Report:
    """Run all cases. Measure each stage, the pack, the budget rule and determinism."""
    sums: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list[float]))
    pack_recalls: dict[str, list[float]] = defaultdict(list[float])
    times: dict[str, list[float]] = defaultdict(list[float])
    violations: list[str] = []
    changing: list[str] = []
    for case in cases:
        recipe = rag.context_recipe(case.context)
        if case.rag not in {source.rag for source in recipe.context.sources}:
            raise ConfigError(f"The case '{case.id}' names a RAG that is not in its context.")
        query = _query(case, recipe.context.query_builder)
        relevant = case.relevant_ids()
        start = time.perf_counter()
        candidates = list(rag.search(case.rag, query, context=case.context).candidates)
        times["retrieve"].append((time.perf_counter() - start) * 1000)
        for stage in STAGES:
            order = _ids(stage_order(candidates, stage), case.by_document)
            sums[case.kind][f"recall_{stage}"].append(recall_at(order, relevant, K))
            sums[case.kind][f"ndcg_{stage}"].append(ndcg_at(order, relevant, K))
            sums[case.kind][f"mrr_{stage}"].append(reciprocal_rank(order, relevant))

        request = ContextRequest(case.context, question=case.question, log_lines=tuple(case.log_lines))
        start = time.perf_counter()
        pack = rag.compose(request)
        times["compose"].append((time.perf_counter() - start) * 1000)
        packed = [chunk.doc_id if case.by_document else chunk.chunk_id for chunk in pack.chunks]
        pack_recalls[case.kind].append(recall_at(list(dict.fromkeys(packed)), relevant, max(len(packed), 1)))

        # The renderer text is inside the messages, so only the template and message overheads are added.
        # The prompt must fit the window without the reserved output and without the safety margin.
        profile = rag.generator_profile(pack.generator)
        rendered = sum(rag.count_tokens(message.content, pack.generator) for message in pack.messages)
        prompt = rendered + profile.template_overhead + profile.per_message_overhead * len(pack.messages)
        if prompt > pack.budget.window - pack.budget.reserved_output - pack.budget.margin:
            violations.append(case.id)
        again = rag.compose(request)
        if [c.chunk_id for c in again.chunks] != [c.chunk_id for c in pack.chunks] or again.messages != pack.messages:
            changing.append(case.id)

    kinds: dict[str, KindReport] = {}
    for kind, values in sorted(sums.items()):
        report = KindReport(cases=len(pack_recalls[kind]), pack_recall=round(statistics.fmean(pack_recalls[kind]), 4))
        for name, series in values.items():
            metric, stage = name.split("_", 1)
            target = {"recall": report.recall, "ndcg": report.ndcg, "mrr": report.mrr}[metric]
            target[stage] = round(statistics.fmean(series), 4)
        kinds[kind] = report
    timing = {
        f"{name}_p{int(share * 100)}": round(percentile(series, share), 1)
        for name, series in times.items()
        for share in (0.5, 0.95)
    }
    return Report(kinds, violations, changing, timing)


def report_json(report: Report) -> dict[str, Any]:
    """Return the report as JSON."""
    return {
        "kinds": {kind: asdict(item) for kind, item in report.kinds.items()},
        "budget_violations": report.budget_violations,
        "nondeterministic": report.nondeterministic,
        "timing_ms": report.timing_ms,
    }


def _bp(value: object) -> int | None:
    return round(float(value) * 10_000) if isinstance(value, int | float) else None


def gate(report: Report, baseline: Mapping[str, Any]) -> list[str]:
    """Return the problems that fail the gate. An empty list passes.

    The gate fails on a budget or determinism problem, on a kind that is in only one of the two
    reports, and when recall@10 or pack recall of a kind drops by more than 2 points.
    """
    problems = [f"budget rule failed for case {case}" for case in report.budget_violations]
    problems += [f"the pack of case {case} changed between two runs" for case in report.nondeterministic]
    raw_kinds: object = baseline.get("kinds")
    if not isinstance(raw_kinds, dict):
        return [*problems, "the baseline has no 'kinds' object"]
    old_kinds = cast(dict[str, Any], raw_kinds)
    problems += [f"the kind '{kind}' is not in the baseline" for kind in sorted(set(report.kinds) - set(old_kinds))]
    problems += [f"the kind '{kind}' is missing from this run" for kind in sorted(set(old_kinds) - set(report.kinds))]
    for kind, item in report.kinds.items():
        old: object = old_kinds.get(kind)
        if not isinstance(old, dict):
            continue
        entry = cast(dict[str, Any], old)
        recall: object = entry.get("recall")
        old_recall = cast(dict[str, Any], recall).get("reranked") if isinstance(recall, dict) else None
        pairs: dict[str, tuple[object, object]] = {
            f"recall@{K}": (old_recall, item.recall.get("reranked")),
            "pack recall": (entry.get("pack_recall"), item.pack_recall),
        }
        for name, (before, now) in pairs.items():
            before_bp, now_bp = _bp(before), _bp(now)
            if before_bp is None or now_bp is None:
                problems.append(f"the {name} of '{kind}' is missing from the baseline or the run")
            elif now_bp < before_bp - MAX_RECALL_DROP_BP:
                problems.append(
                    f"the {name} of '{kind}' dropped from {before_bp / 10_000:.4f} to {now_bp / 10_000:.4f}"
                )
    return problems


def write_json(path: Path, data: Mapping[str, Any]) -> None:
    """Write JSON with sorted keys."""
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", "utf-8")
