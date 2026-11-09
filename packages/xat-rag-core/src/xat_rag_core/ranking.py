"""The merge of the ranked lists of several RAGs into one list."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from xat_rag_core.errors import InternalError
from xat_rag_core.model import Candidate

MAX_CHUNKS = 100


class OmitReason(StrEnum):
    """Why a candidate is not in the result."""

    DUPLICATE = "duplicate"
    LIMIT = "limit"
    LOW_SCORE = "low_score"


@dataclass(frozen=True, slots=True)
class MergeResult:
    """The chosen candidates, best first, and the chunk ID and reason of each omitted candidate."""

    chosen: tuple[Candidate, ...]
    omitted: tuple[tuple[str, OmitReason], ...]


def final_score(candidate: Candidate) -> float:
    """Return the rerank score of a candidate, or its RRF score if the RAG has no reranker."""
    scores = candidate.scores
    return scores.rerank_score if scores.rerank_score is not None else (scores.rrf_score or 0.0)


def merge_ranked(by_rag: Sequence[Sequence[Candidate]], limit: int, *, min_score: float | None = None) -> MergeResult:
    """Sort all candidates by score and then by chunk ID. Keep one copy of each chunk ID, and cut to `limit`.

    A candidate with a score below `min_score` is omitted. A score equal to `min_score` is kept.
    """
    candidates = [candidate for candidates in by_rag for candidate in candidates]
    if any(math.isnan(final_score(candidate)) for candidate in candidates):
        raise InternalError("A candidate has a score that is not a number.")
    candidates.sort(key=lambda candidate: (-final_score(candidate), candidate.chunk_id))
    chosen: list[Candidate] = []
    omitted: list[tuple[str, OmitReason]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.chunk_id in seen:
            omitted.append((candidate.chunk_id, OmitReason.DUPLICATE))
        elif min_score is not None and final_score(candidate) < min_score:
            omitted.append((candidate.chunk_id, OmitReason.LOW_SCORE))
        elif len(chosen) >= limit:
            omitted.append((candidate.chunk_id, OmitReason.LIMIT))
        else:
            seen.add(candidate.chunk_id)
            chosen.append(candidate)
    return MergeResult(tuple(chosen), tuple(omitted))
