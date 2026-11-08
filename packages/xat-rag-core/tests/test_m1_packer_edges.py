import pytest
from conftest import make_chunk
from xat_rag_core.errors import InvalidRequestError
from xat_rag_core.model import Candidate
from xat_rag_core.packer import OmitReason, PackItem, SourceQuota, order_edges, pack


def _item(chunk_id: str, score: float, tokens: int, rag: str = "docs") -> PackItem:
    return PackItem(Candidate(make_chunk(chunk_id, rag=rag)), score, tokens)


def _ids(items: tuple[PackItem, ...]) -> list[str]:
    return [item.candidate.chunk_id for item in items]


def test_zero_budget_packs_nothing_and_lowers_floors() -> None:
    items = {"docs": [_item("docs:a#1", 0.9, 10)]}
    result = pack(items, [SourceQuota("docs", 50)], budget=0)
    assert result.items == ()
    assert result.used_tokens == 0
    assert result.floors_lowered
    assert [omitted.chunk_id for omitted in result.omitted] == ["docs:a#1"]


def test_zero_budget_without_floors_does_not_lower_floors() -> None:
    result = pack({"docs": [_item("docs:a#1", 0.9, 10)]}, [SourceQuota("docs")], budget=0)
    assert result.items == ()
    assert not result.floors_lowered


def test_negative_budget_is_refused() -> None:
    with pytest.raises(InvalidRequestError):
        pack({}, [SourceQuota("docs")], budget=-1)


def test_empty_input_gives_an_empty_result() -> None:
    result = pack({}, [SourceQuota("docs", 10)], budget=100)
    assert result.items == ()
    assert result.omitted == ()
    assert result.used_tokens == 0


def test_item_larger_than_the_budget_is_omitted_and_smaller_items_still_fit() -> None:
    items = {"docs": [_item("docs:big#1", 0.99, 500), _item("docs:small#1", 0.5, 40)]}
    result = pack(items, [SourceQuota("docs")], budget=100)
    assert _ids(result.items) == ["docs:small#1"]
    assert [omitted.chunk_id for omitted in result.omitted] == ["docs:big#1"]


def test_item_larger_than_the_budget_has_the_reason_budget() -> None:
    result = pack({"docs": [_item("docs:big#1", 0.99, 500)]}, [SourceQuota("docs")], budget=100)
    assert [omitted.reason for omitted in result.omitted] == [OmitReason.BUDGET]


def test_cap_smaller_than_floor_keeps_the_cap() -> None:
    items = {"docs": [_item(f"docs:a#{i}", 0.9 - i / 100, 20) for i in range(10)]}
    result = pack(items, [SourceQuota("docs", min_tokens=200, max_share=0.1)], budget=300)
    assert result.used_tokens == 20  # The cap is 30 tokens, so only one item of 20 tokens fits.
    assert len(result.items) == 1
    assert not result.floors_lowered
    assert {omitted.reason for omitted in result.omitted} == {OmitReason.QUOTA}


def test_floor_phase_skips_an_item_that_does_not_fit_the_cap_and_takes_the_next() -> None:
    items = {"docs": [_item("docs:big#1", 0.9, 100), _item("docs:small#1", 0.1, 10)]}
    result = pack(items, [SourceQuota("docs", min_tokens=100, max_share=0.2)], budget=200)
    assert _ids(result.items) == ["docs:small#1"]


def test_floor_is_not_filled_beyond_the_floor_before_other_sources_compete() -> None:
    items = {
        "docs": [_item(f"docs:a#{i}", 0.9, 50) for i in range(4)],
        "jira": [_item("jira:b#1", 0.2, 50, "jira")],
    }
    result = pack(items, [SourceQuota("docs", 50), SourceQuota("jira", 50)], budget=100)
    assert "jira:b#1" in _ids(result.items)
    assert result.used_tokens == 100


def test_floors_lowered_in_proportion_never_exceed_the_budget() -> None:
    items = {
        "a": [_item(f"a:x#{i}", 0.9, 30, "a") for i in range(5)],
        "b": [_item(f"b:x#{i}", 0.8, 30, "b") for i in range(5)],
    }
    result = pack(items, [SourceQuota("a", 300), SourceQuota("b", 100)], budget=100)
    assert result.floors_lowered
    assert result.used_tokens <= 100
    assert result.used_tokens == sum(item.tokens for item in result.items)


def test_same_chunk_id_in_two_rags_is_packed_once_when_only_the_pool_phase_runs() -> None:
    items = {"a": [_item("x#1", 0.9, 10, "a")], "b": [_item("x#1", 0.8, 10, "b")]}
    result = pack(items, [SourceQuota("a"), SourceQuota("b")], budget=100)
    assert _ids(result.items) == ["x#1"]
    assert result.used_tokens == 10
    assert [(o.chunk_id, o.reason) for o in result.omitted] == [("x#1", OmitReason.DUPLICATE)]


def test_same_chunk_id_in_two_rags_is_packed_once_when_both_have_floors() -> None:
    items = {"a": [_item("x#1", 0.9, 10, "a")], "b": [_item("x#1", 0.8, 10, "b")]}
    result = pack(items, [SourceQuota("a", 10), SourceQuota("b", 10)], budget=100)
    assert _ids(result.items) == ["x#1"]
    assert result.used_tokens == 10


def test_same_chunk_id_twice_in_one_rag_with_a_floor_is_packed_once() -> None:
    items = {"docs": [_item("x#1", 0.9, 10), _item("x#1", 0.8, 10)]}
    result = pack(items, [SourceQuota("docs", 20)], budget=100)
    assert _ids(result.items) == ["x#1"]


def test_same_chunk_id_twice_in_one_rag_without_a_floor_is_packed_once() -> None:
    items = {"docs": [_item("x#1", 0.9, 10), _item("x#1", 0.8, 10)]}
    result = pack(items, [SourceQuota("docs")], budget=100)
    assert _ids(result.items) == ["x#1"]
    assert [omitted.reason for omitted in result.omitted] == [OmitReason.DUPLICATE]


def test_cap_is_not_lowered_by_float_error() -> None:
    result = pack({"docs": [_item("docs:a#1", 0.9, 29)]}, [SourceQuota("docs", 0, 0.29)], budget=100)
    assert _ids(result.items) == ["docs:a#1"]


def test_zero_token_item_fits_a_zero_budget() -> None:
    result = pack({"docs": [_item("docs:a#1", 0.9, 0)]}, [SourceQuota("docs")], budget=0)
    assert _ids(result.items) == ["docs:a#1"]


def test_used_tokens_equals_the_budget_for_an_exact_fit() -> None:
    items = {"docs": [_item(f"docs:a#{i}", 0.9 - i / 100, 25) for i in range(6)]}
    result = pack(items, [SourceQuota("docs")], budget=100)
    assert result.used_tokens == 100
    assert len(result.items) == 4


def test_equal_scores_are_ordered_by_chunk_id() -> None:
    items = {"docs": [_item("docs:c#1", 0.5, 10), _item("docs:a#1", 0.5, 10), _item("docs:b#1", 0.5, 10)]}
    result = pack(items, [SourceQuota("docs")], budget=100)
    assert _ids(result.items) == ["docs:a#1", "docs:c#1", "docs:b#1"]


def test_unknown_rag_is_refused_even_when_other_rags_are_known() -> None:
    with pytest.raises(InvalidRequestError):
        pack({"docs": [], "ghost": []}, [SourceQuota("docs")], budget=10)


def test_rag_with_a_quota_but_no_items_is_fine() -> None:
    result = pack({"docs": [_item("docs:a#1", 0.9, 10)]}, [SourceQuota("docs"), SourceQuota("jira", 50)], budget=100)
    assert _ids(result.items) == ["docs:a#1"]


@pytest.mark.parametrize(
    ("min_tokens", "max_share"),
    [(-1, 1.0), (0, 0.0), (0, -0.5), (0, 1.5), (0, float("nan"))],
)
def test_bad_quota_values_are_refused(min_tokens: int, max_share: float) -> None:
    with pytest.raises(InvalidRequestError):
        SourceQuota("docs", min_tokens, max_share)


@pytest.mark.parametrize(
    ("count", "expected"),
    [(1, [0]), (2, [0, 1]), (3, [0, 2, 1]), (6, [0, 2, 4, 5, 3, 1])],
)
def test_order_edges_puts_the_two_best_items_at_the_ends(count: int, expected: list[int]) -> None:
    assert order_edges(list(range(count))) == expected
