import numpy as np
from xat_rag_core.mmr import mmr_order, remove_duplicates


def _unit(*rows: list[float]) -> np.ndarray:
    array = np.asarray(rows, dtype=np.float32)
    return array / np.linalg.norm(array, axis=1, keepdims=True)


def test_remove_exact_and_near_duplicates() -> None:
    vectors = _unit([1, 0], [1, 0.01], [0, 1], [0.5, 0.5])
    assert remove_duplicates(["h1", "h2", "h3", "h3"], vectors) == [0, 2]


def test_mmr_prefers_diversity() -> None:
    vectors = _unit([1, 0], [1, 0.05], [0, 1])
    assert mmr_order([0.9, 0.89, 0.5], vectors, limit=3, lambda_=0.5) == [0, 2, 1]


def test_mmr_with_lambda_one_keeps_relevance_order() -> None:
    vectors = _unit([1, 0], [1, 0.05], [0, 1])
    assert mmr_order([0.9, 0.8, 0.5], vectors, limit=2, lambda_=1.0) == [0, 1]


def test_mmr_empty_and_equal_relevance() -> None:
    assert mmr_order([], np.zeros((0, 2), dtype=np.float32), limit=3) == []
    vectors = _unit([1, 0], [0, 1])
    assert mmr_order([0.5, 0.5], vectors, limit=2) == [0, 1]
