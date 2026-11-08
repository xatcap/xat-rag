"""Duplicate removal and Maximal Marginal Relevance (MMR)."""

from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

from xat_rag_core.errors import InternalError

DEFAULT_MMR_LAMBDA = 0.7
DEFAULT_NEAR_DUPLICATE = 0.95


def remove_duplicates(
    content_hashes: Sequence[str],
    vectors: NDArray[np.float32],
    near_duplicate: float = DEFAULT_NEAR_DUPLICATE,
) -> list[int]:
    """Return the indexes to keep, in input order. Inputs must be sorted best first.

    An item is a duplicate when an earlier item has the same content hash, or when the cosine
    similarity of their vectors is at least `near_duplicate`. Vectors must be normalized.
    """
    _check(len(content_hashes), vectors)
    kept: list[int] = []
    hashes: set[str] = set()
    for index, content_hash in enumerate(content_hashes):
        if content_hash in hashes:
            continue
        if kept and float(np.max(vectors[kept] @ vectors[index])) >= near_duplicate:
            continue
        hashes.add(content_hash)
        kept.append(index)
    return kept


def mmr_order(
    relevance: Sequence[float],
    vectors: NDArray[np.float32],
    limit: int,
    lambda_: float = DEFAULT_MMR_LAMBDA,
) -> list[int]:
    """Return up to `limit` indexes in MMR order.

    MMR = lambda * relevance - (1 - lambda) * the highest similarity to an item already chosen.
    Inputs must be sorted best first. Ties go to the earlier item, so the result is deterministic.
    Relevance values are scaled to the range 0 to 1 first.
    """
    count = len(relevance)
    _check(count, vectors)
    if not 0.0 <= lambda_ <= 1.0:
        raise InternalError("The MMR lambda must be between 0 and 1.")
    if count == 0 or limit <= 0:
        return []
    rel = np.asarray(relevance, dtype=np.float64)
    span = float(rel.max() - rel.min())
    rel = (rel - rel.min()) / span if span > 0 else np.ones_like(rel)
    chosen: list[int] = []
    best_similarity = np.full(count, -np.inf)
    remaining = np.ones(count, dtype=bool)
    matrix = vectors.astype(np.float64)
    while len(chosen) < min(limit, count):
        penalty = np.where(np.isfinite(best_similarity), best_similarity, 0.0)
        score = lambda_ * rel - (1.0 - lambda_) * penalty
        score[~remaining] = -np.inf
        pick = int(np.argmax(score))  # argmax returns the first index on a tie.
        chosen.append(pick)
        remaining[pick] = False
        similarity = matrix @ matrix[pick]
        best_similarity = np.maximum(best_similarity, similarity)
    return chosen


def _check(count: int, vectors: NDArray[np.float32]) -> None:
    if vectors.ndim != 2 or vectors.shape[0] != count:
        raise InternalError("The vectors must be a 2-D array with one row for each item.")
