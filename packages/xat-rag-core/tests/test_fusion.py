import pytest
from xat_rag_core.errors import InternalError
from xat_rag_core.fusion import reciprocal_rank_fusion


def test_rrf_scores_and_ranks() -> None:
    fused = reciprocal_rank_fusion({"lexical": ["a", "b", "c"], "dense": ["b", "a", "d"]}, k=60)
    assert [item.chunk_id for item in fused] == ["a", "b", "c", "d"]
    assert fused[0].score == pytest.approx(1 / 61 + 1 / 62)
    assert fused[0].ranks == {"lexical": 1, "dense": 2}
    assert fused[3].ranks == {"dense": 3}


def test_ties_are_broken_by_chunk_id() -> None:
    fused = reciprocal_rank_fusion({"x": ["z"], "y": ["a"]})
    assert [item.chunk_id for item in fused] == ["a", "z"]


def test_repeated_id_counts_once_at_best_rank() -> None:
    fused = reciprocal_rank_fusion({"x": ["a", "a", "b"]})
    assert fused[1].ranks == {"x": 2}


def test_bad_k_is_refused() -> None:
    with pytest.raises(InternalError, match="k"):
        reciprocal_rank_fusion({}, k=0)


def test_equal_rank_sets_tie_on_chunk_id() -> None:
    # a has ranks (first 40, lexical 39, dense 32), b has (first 32, lexical 40, dense 39).
    def at(positions: dict[int, str], prefix: str) -> list[str]:
        return [positions.get(rank, f"{prefix}{rank}") for rank in range(1, 41)]

    first = at({32: "b", 40: "a"}, "x")
    lexical = at({39: "a", 40: "b"}, "l")
    dense = at({32: "a", 39: "b"}, "d")
    fused = {
        item.chunk_id: item for item in reciprocal_rank_fusion({"first": first, "lexical": lexical, "dense": dense})
    }
    assert fused["a"].score == fused["b"].score
    order = [item.chunk_id for item in reciprocal_rank_fusion({"first": first, "lexical": lexical, "dense": dense})]
    assert order.index("a") < order.index("b")
