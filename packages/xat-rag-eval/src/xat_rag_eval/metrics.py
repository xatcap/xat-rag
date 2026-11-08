"""Retrieval metrics: recall@k, nDCG@k and MRR, with graded relevance."""

import math
from collections.abc import Mapping, Sequence


def recall_at(ranked: Sequence[str], relevant: Mapping[str, int], k: int) -> float:
    """Return the share of relevant items in the first `k` results. 1.0 when nothing is relevant."""
    if not relevant:
        return 1.0
    found = sum(1 for item in ranked[:k] if item in relevant)
    return found / len(relevant)


def ndcg_at(ranked: Sequence[str], relevant: Mapping[str, int], k: int) -> float:
    """Return nDCG@k with gains of 2^grade - 1."""
    if not relevant:
        return 1.0
    dcg = sum((2 ** relevant.get(item, 0) - 1) / math.log2(position + 2) for position, item in enumerate(ranked[:k]))
    ideal = sorted(relevant.values(), reverse=True)[:k]
    idcg = sum((2**grade - 1) / math.log2(position + 2) for position, grade in enumerate(ideal))
    return dcg / idcg if idcg > 0 else 0.0


def reciprocal_rank(ranked: Sequence[str], relevant: Mapping[str, int]) -> float:
    """Return 1 / the rank of the first relevant item, or 0."""
    for position, item in enumerate(ranked, start=1):
        if item in relevant:
            return 1.0 / position
    return 0.0


def percentile(values: Sequence[float], share: float) -> float:
    """Return the value at `share` (0 to 1) of the sorted values, by the nearest rank."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(share * len(ordered)) - 1))
    return ordered[index]
