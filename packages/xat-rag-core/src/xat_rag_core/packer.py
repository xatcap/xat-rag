"""The budget packer: quotas for each source, an exact fit, and the "edges" order. See ADR 0007."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from xat_rag_core.errors import InternalError, InvalidRequestError
from xat_rag_core.model import Candidate


class OmitReason(StrEnum):
    """Why the packer did not use a candidate."""

    BUDGET = "budget"
    QUOTA = "quota"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class SourceQuota:
    """The quota of one RAG in a context: a floor of tokens and a maximum share of the budget."""

    rag: str
    min_tokens: int = 0
    max_share: float = 1.0

    def __post_init__(self) -> None:
        """Check the values."""
        if self.min_tokens < 0 or not 0.0 < self.max_share <= 1.0:
            raise InvalidRequestError("A quota needs min_tokens >= 0 and a max_share above 0 and up to 1.")


@dataclass(frozen=True, slots=True)
class PackItem:
    """A candidate with its score and its token count as rendered, with its block wrapper."""

    candidate: Candidate
    score: float
    tokens: int


@dataclass(frozen=True, slots=True)
class Omitted:
    """A candidate that the packer did not use."""

    chunk_id: str
    reason: OmitReason


@dataclass(frozen=True, slots=True)
class PackResult:
    """The packed items in prompt order, and what was left out."""

    items: tuple[PackItem, ...]
    used_tokens: int
    floors_lowered: bool
    omitted: tuple[Omitted, ...]


def _empty_counts() -> dict[str, int]:
    return {}


def _empty_items() -> list[PackItem]:
    return []


def _empty_ids() -> set[str]:
    return set()


def _sort_key(item: PackItem) -> tuple[float, str]:
    return (-item.score, item.candidate.chunk_id)


def order_edges[T](items_best_first: Sequence[T]) -> list[T]:
    """Put the strongest item first and the second strongest last. The weakest items go in the middle."""
    front = list(items_best_first[0::2])
    back = list(items_best_first[1::2])
    return front + back[::-1]


@dataclass(slots=True)
class _State:
    budget: int
    caps: dict[str, int]
    floors: dict[str, int]
    used: int = 0
    used_by_rag: dict[str, int] = field(default_factory=_empty_counts)
    chosen: list[PackItem] = field(default_factory=_empty_items)
    chosen_ids: set[str] = field(default_factory=_empty_ids)

    def unmet_floors_of_others(self, rag: str) -> int:
        return sum(
            max(0, floor - self.used_by_rag.get(other, 0)) for other, floor in self.floors.items() if other != rag
        )

    def fits(self, rag: str, item: PackItem, reserve: int) -> bool:
        return (
            self.used + item.tokens + reserve <= self.budget
            and self.used_by_rag.get(rag, 0) + item.tokens <= self.caps[rag]
        )

    def take(self, rag: str, item: PackItem) -> None:
        self.chosen.append(item)
        self.chosen_ids.add(item.candidate.chunk_id)
        self.used += item.tokens
        self.used_by_rag[rag] = self.used_by_rag.get(rag, 0) + item.tokens

    def reason(self, rag: str, item: PackItem) -> OmitReason:
        if item.candidate.chunk_id in self.chosen_ids:
            return OmitReason.DUPLICATE
        if item.tokens > self.budget or self.used_by_rag.get(rag, 0) + item.tokens <= self.caps[rag]:
            return OmitReason.BUDGET
        return OmitReason.QUOTA


def _check_inputs(items_by_rag: Mapping[str, Sequence[PackItem]], quotas: Sequence[SourceQuota], budget: int) -> None:
    if budget < 0:
        raise InvalidRequestError("The budget must not be negative.")
    rags = [quota.rag for quota in quotas]
    if len(set(rags)) != len(rags):
        raise InvalidRequestError("A context has two quotas for the same RAG.")
    if set(items_by_rag) - set(rags):
        raise InvalidRequestError("Each RAG in a context needs a quota.")
    for items in items_by_rag.values():
        for item in items:
            if item.tokens < 0 or math.isnan(item.score):
                raise InternalError("A pack item has negative tokens or a score that is not a number.")


def _floors(
    items_by_rag: Mapping[str, Sequence[PackItem]], quotas: Sequence[SourceQuota], budget: int, caps: Mapping[str, int]
) -> tuple[dict[str, int], bool]:
    """Return the floors of the RAGs that have hits, lowered in proportion if they do not fit."""
    requested = {q.rag: q.min_tokens for q in quotas if items_by_rag.get(q.rag)}
    lowered = sum(requested.values()) > budget
    floors = {rag: min(value, caps[rag]) for rag, value in requested.items()}
    total = sum(floors.values())
    if total > budget:
        floors = {rag: math.floor(value * budget / total) for rag, value in floors.items()}
    return floors, lowered


def pack(items_by_rag: Mapping[str, Sequence[PackItem]], quotas: Sequence[SourceQuota], budget: int) -> PackResult:
    """Choose items that fit the budget exactly, and return them in "edges" order.

    1. Each RAG with hits first gets items, best first, up to its floor (`min_tokens`). An item
       is taken only if the floors of the other RAGs still fit.
    2. Then all other items compete by score. A RAG never uses more than `max_share` of the budget.
    3. If the budget is smaller than the sum of the floors, the floors go down in proportion.

    A chunk ID is packed once. A repeat gets the reason `duplicate`. Each candidate that is not
    packed is in `omitted`, with its reason.
    """
    _check_inputs(items_by_rag, quotas, budget)
    # Round before the floor, so that 0.29 x 100 gives 29 and not 28 (a float error).
    caps = {quota.rag: math.floor(round(quota.max_share * budget, 6)) for quota in quotas}
    floors, floors_lowered = _floors(items_by_rag, quotas, budget, caps)
    state = _State(budget=budget, caps=caps, floors=floors)
    pending = [(rag, item) for rag, items in items_by_rag.items() for item in sorted(items, key=_sort_key)]

    for quota in quotas:
        for rag, item in pending:
            if rag != quota.rag or state.used_by_rag.get(rag, 0) >= floors.get(rag, 0):
                continue
            if item.candidate.chunk_id not in state.chosen_ids and state.fits(
                rag, item, state.unmet_floors_of_others(rag)
            ):
                state.take(rag, item)

    taken = {id(item) for item in state.chosen}
    omitted: list[Omitted] = []
    for rag, item in sorted(((r, i) for r, i in pending if id(i) not in taken), key=lambda pair: _sort_key(pair[1])):
        if item.candidate.chunk_id not in state.chosen_ids and state.fits(rag, item, 0):
            state.take(rag, item)
        else:
            omitted.append(Omitted(item.candidate.chunk_id, state.reason(rag, item)))

    ordered = order_edges(sorted(state.chosen, key=_sort_key))
    return PackResult(tuple(ordered), state.used, floors_lowered, tuple(omitted))
