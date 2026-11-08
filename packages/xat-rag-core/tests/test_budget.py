import pytest
from xat_rag_core.budget import GeneratorProfile, compute_budget
from xat_rag_core.errors import ContextTooLargeError, InvalidRequestError

PROFILE = GeneratorProfile("ministral-3-3b", 8192, "tekken@x", template_overhead=40, per_message_overhead=10)


def test_budget_matches_the_formula() -> None:
    budget = compute_budget(
        PROFILE, window=8192, reserved_output=768, pinned_tokens=310, input_tokens=640, renderer_tokens=50
    )
    assert budget.margin == 246  # ceil(8192 * 0.03)
    assert budget.overhead == 40 + 2 * 10 + 50
    assert budget.chunks == 8192 - 768 - 110 - 310 - 640 - 246


def test_too_large_fixed_parts_are_refused() -> None:
    with pytest.raises(ContextTooLargeError):
        compute_budget(PROFILE, window=1024, reserved_output=768, pinned_tokens=300, input_tokens=0)


def test_window_above_the_generator_is_refused() -> None:
    with pytest.raises(InvalidRequestError):
        compute_budget(PROFILE, window=16384, reserved_output=768, pinned_tokens=0, input_tokens=0)
