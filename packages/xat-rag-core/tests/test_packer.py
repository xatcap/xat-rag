import pytest
from conftest import make_chunk
from xat_rag_core.errors import InvalidRequestError
from xat_rag_core.model import Candidate
from xat_rag_core.packer import OmitReason, PackItem, SourceQuota, order_edges, pack


def _item(chunk_id: str, score: float, tokens: int, rag: str = "docs") -> PackItem:
    return PackItem(Candidate(make_chunk(chunk_id, rag=rag)), score, tokens)


def test_order_edges() -> None:
    assert order_edges([1, 2, 3, 4, 5]) == [1, 3, 5, 4, 2]
    assert order_edges([]) == []


def test_pack_never_exceeds_the_budget_and_respects_shares() -> None:
    items = {
        "docs": [_item(f"docs:a#{i}", 1.0 - i / 10, 100) for i in range(8)],
        "jira": [_item(f"jira:b#{i}", 0.95 - i / 10, 100, "jira") for i in range(8)],
    }
    result = pack(items, [SourceQuota("docs", 0, 0.5), SourceQuota("jira", 0, 0.5)], budget=650)
    assert result.used_tokens <= 650
    assert result.used_tokens == sum(item.tokens for item in result.items)
    docs_tokens = sum(item.tokens for item in result.items if item.candidate.chunk.rag == "docs")
    assert docs_tokens <= 325
    assert any(omitted.reason is OmitReason.QUOTA for omitted in result.omitted)


def test_floors_guarantee_a_weak_source() -> None:
    items = {
        "docs": [_item(f"docs:a#{i}", 0.9, 100) for i in range(5)],
        "diffs": [_item("git:r#h1", 0.1, 100, "diffs")],
    }
    result = pack(items, [SourceQuota("docs", 0, 1.0), SourceQuota("diffs", 100, 1.0)], budget=300)
    assert "git:r#h1" in {item.candidate.chunk_id for item in result.items}
    assert not result.floors_lowered


def test_floors_go_down_when_the_budget_is_small() -> None:
    items = {"docs": [_item("docs:a#1", 0.9, 50)], "jira": [_item("jira:b#1", 0.8, 50, "jira")]}
    result = pack(items, [SourceQuota("docs", 400), SourceQuota("jira", 400)], budget=100)
    assert result.floors_lowered
    assert result.used_tokens == 100


def test_order_is_deterministic_and_strongest_first() -> None:
    items = {"docs": [_item("docs:b#1", 0.5, 10), _item("docs:a#1", 0.5, 10), _item("docs:c#1", 0.9, 10)]}
    result = pack(items, [SourceQuota("docs")], budget=100)
    assert [item.candidate.chunk_id for item in result.items] == ["docs:c#1", "docs:b#1", "docs:a#1"]


def test_rag_without_quota_is_refused() -> None:
    with pytest.raises(InvalidRequestError):
        pack({"x": []}, [], budget=10)


def test_a_large_item_does_not_take_the_floor_of_another_rag() -> None:
    items = {"docs": [_item("docs:a#1", 0.9, 300)], "jira": [_item("jira:b#1", 0.1, 100, "jira")]}
    result = pack(items, [SourceQuota("docs", 100), SourceQuota("jira", 100)], budget=300)
    assert [item.candidate.chunk_id for item in result.items] == ["jira:b#1"]
    assert result.omitted[0].chunk_id == "docs:a#1"


def test_a_rag_without_hits_does_not_lower_floors() -> None:
    items = {"docs": [_item("docs:a#1", 0.9, 50)], "jira": []}
    result = pack(items, [SourceQuota("docs", 400), SourceQuota("jira", 400)], budget=500)
    assert not result.floors_lowered


def test_duplicate_of_a_floor_item_gets_a_reason() -> None:
    items = {"docs": [_item("docs:x#1", 0.9, 5), _item("docs:x#1", 0.9, 5)]}
    result = pack(items, [SourceQuota("docs", 5)], budget=100)
    assert len(result.items) == 1
    assert [o.reason for o in result.omitted] == [OmitReason.DUPLICATE]
