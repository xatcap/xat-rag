"""Reciprocal Rank Fusion (RRF). Ties are broken by chunk ID, so the order is deterministic."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from xat_rag_core.errors import InternalError

DEFAULT_RRF_K = 60


def _empty_ranks() -> dict[str, int]:
    return {}


@dataclass(frozen=True, slots=True)
class FusedItem:
    """One fused item, with its RRF score and its 1-based rank in each list."""

    chunk_id: str
    score: float
    ranks: Mapping[str, int] = field(default_factory=_empty_ranks)


def reciprocal_rank_fusion(ranked: Mapping[str, Sequence[str]], k: int = DEFAULT_RRF_K) -> list[FusedItem]:
    """Merge ranked lists of chunk IDs with RRF: score = sum of 1 / (k + rank).

    `ranked` maps a list name (for example `lexical`) to chunk IDs, best first. A chunk ID that
    appears twice in one list counts only at its best rank. The result is sorted by score,
    highest first, and then by chunk ID.
    """
    if k < 1:
        raise InternalError("The RRF constant k must be at least 1.")
    terms: dict[str, list[float]] = {}
    ranks: dict[str, dict[str, int]] = {}
    for name, ids in ranked.items():
        seen: set[str] = set()
        rank = 0
        for chunk_id in ids:
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            rank += 1
            terms.setdefault(chunk_id, []).append(1.0 / (k + rank))
            ranks.setdefault(chunk_id, {})[name] = rank
    # fsum gives the exact sum, so equal rank sets give equal scores and the chunk ID breaks the tie.
    items = [FusedItem(chunk_id, math.fsum(values), ranks[chunk_id]) for chunk_id, values in terms.items()]
    items.sort(key=lambda item: (-item.score, item.chunk_id))
    return items
