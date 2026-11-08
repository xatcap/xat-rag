import pytest
from xat_rag_core.budget import GeneratorProfile, compute_budget
from xat_rag_core.errors import ContextTooLargeError, InvalidRequestError

PROFILE = GeneratorProfile("small", 8192, "tok@x", template_overhead=40, per_message_overhead=10)


def test_default_message_count_is_two() -> None:
    default = compute_budget(PROFILE, window=4096, reserved_output=0, pinned_tokens=0, input_tokens=0)
    explicit = compute_budget(PROFILE, window=4096, reserved_output=0, pinned_tokens=0, input_tokens=0, message_count=2)
    assert default == explicit
    assert default.overhead == 40 + 2 * 10


@pytest.mark.parametrize("message_count", [0, 1, 5])
def test_message_count_changes_the_overhead(message_count: int) -> None:
    budget = compute_budget(
        PROFILE, window=4096, reserved_output=0, pinned_tokens=0, input_tokens=0, message_count=message_count
    )
    assert budget.overhead == 40 + 10 * message_count
    assert budget.chunks == 4096 - budget.overhead - budget.margin


@pytest.mark.parametrize("renderer_tokens", [0, 1, 123])
def test_renderer_tokens_reduce_the_chunk_budget_one_to_one(renderer_tokens: int) -> None:
    base = compute_budget(PROFILE, window=4096, reserved_output=100, pinned_tokens=0, input_tokens=0)
    other = compute_budget(
        PROFILE, window=4096, reserved_output=100, pinned_tokens=0, input_tokens=0, renderer_tokens=renderer_tokens
    )
    assert base.chunks - other.chunks == renderer_tokens
    assert other.overhead - base.overhead == renderer_tokens


def test_breakdown_adds_up_to_the_window() -> None:
    budget = compute_budget(
        PROFILE, window=8192, reserved_output=700, pinned_tokens=200, input_tokens=300, renderer_tokens=60
    )
    total = budget.reserved_output + budget.overhead + budget.pinned + budget.input + budget.margin + budget.chunks
    assert total == budget.window


def test_zero_chunk_budget_is_allowed() -> None:
    probe = compute_budget(PROFILE, window=1000, reserved_output=0, pinned_tokens=0, input_tokens=0)
    exact = compute_budget(PROFILE, window=1000, reserved_output=probe.chunks, pinned_tokens=0, input_tokens=0)
    assert exact.chunks == 0


def test_one_token_too_many_is_refused_and_the_message_names_the_gap() -> None:
    probe = compute_budget(PROFILE, window=1000, reserved_output=0, pinned_tokens=0, input_tokens=0)
    with pytest.raises(ContextTooLargeError, match=r"need 1 more tokens"):
        compute_budget(PROFILE, window=1000, reserved_output=probe.chunks + 1, pinned_tokens=0, input_tokens=0)


def test_renderer_tokens_can_make_the_budget_too_large() -> None:
    with pytest.raises(ContextTooLargeError):
        compute_budget(PROFILE, window=200, reserved_output=0, pinned_tokens=0, input_tokens=0, renderer_tokens=1000)


@pytest.mark.parametrize("window", [0, -1, 8193])
def test_window_outside_the_range_is_refused(window: int) -> None:
    with pytest.raises(InvalidRequestError):
        compute_budget(PROFILE, window=window, reserved_output=0, pinned_tokens=0, input_tokens=0)


def test_window_equal_to_the_generator_window_is_allowed() -> None:
    assert compute_budget(PROFILE, window=8192, reserved_output=0, pinned_tokens=0, input_tokens=0).window == 8192


@pytest.mark.parametrize(
    "field", ["reserved_output", "pinned_tokens", "input_tokens", "message_count", "renderer_tokens"]
)
def test_negative_counts_are_refused(field: str) -> None:
    values = {"reserved_output": 0, "pinned_tokens": 0, "input_tokens": 0, "message_count": 2, "renderer_tokens": 0}
    values[field] = -1
    with pytest.raises(InvalidRequestError):
        compute_budget(PROFILE, window=4096, **values)


def test_margin_is_rounded_up() -> None:
    profile = GeneratorProfile("p", 1000, "t", 0, 0, safety_margin_pct=3.0)
    assert compute_budget(profile, window=101, reserved_output=0, pinned_tokens=0, input_tokens=0).margin == 4


def test_zero_margin_percent_gives_no_margin() -> None:
    profile = GeneratorProfile("p", 1000, "t", 0, 0, safety_margin_pct=0.0)
    budget = compute_budget(profile, window=1000, reserved_output=0, pinned_tokens=0, input_tokens=0)
    assert budget.margin == 0
    assert budget.chunks == 1000
