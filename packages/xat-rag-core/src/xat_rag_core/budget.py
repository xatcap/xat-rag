"""Generator profiles and the token budget for chunks. See ADR 0007."""

import math
from dataclasses import dataclass

from xat_rag_core.errors import ContextTooLargeError, InvalidRequestError


@dataclass(frozen=True, slots=True)
class GeneratorProfile:
    """The facts about a generator that the budget needs. The overheads are measured, not guessed."""

    name: str
    context_window: int
    tokenizer_id: str
    template_overhead: int
    per_message_overhead: int
    safety_margin_pct: float = 3.0


@dataclass(frozen=True, slots=True)
class BudgetBreakdown:
    """How the window is split. `chunks` is the number of tokens that the packer can use."""

    window: int
    reserved_output: int
    overhead: int
    pinned: int
    input: int
    margin: int
    chunks: int


def compute_budget(
    profile: GeneratorProfile,
    *,
    window: int,
    reserved_output: int,
    pinned_tokens: int,
    input_tokens: int,
    message_count: int = 2,
    renderer_tokens: int = 0,
) -> BudgetBreakdown:
    """Compute the token budget for chunks.

    chunks = window - reserved_output - template_overhead - per_message_overhead x messages
             - renderer_tokens - pinned - input - margin

    `renderer_tokens` counts the fixed text of the renderer, for example the trust note.
    Raise `InvalidRequestError` if the window is larger than the generator allows, and
    `ContextTooLargeError` if the fixed parts do not fit. xat-rag never cuts a pinned item.
    """
    if window < 1 or window > profile.context_window:
        raise InvalidRequestError(
            f"The window must be between 1 and {profile.context_window} tokens for the generator '{profile.name}'."
        )
    if min(reserved_output, pinned_tokens, input_tokens, message_count, renderer_tokens) < 0:
        raise InvalidRequestError("Token counts must not be negative.")
    overhead = profile.template_overhead + profile.per_message_overhead * message_count + renderer_tokens
    margin = math.ceil(window * profile.safety_margin_pct / 100.0)
    chunks = window - reserved_output - overhead - pinned_tokens - input_tokens - margin
    if chunks < 0:
        raise ContextTooLargeError(
            f"The pinned items, the input and the reserved output need {-chunks} more tokens "
            f"than the window of {window} has. Use a shorter input or fewer pinned prompts."
        )
    return BudgetBreakdown(
        window=window,
        reserved_output=reserved_output,
        overhead=overhead,
        pinned=pinned_tokens,
        input=input_tokens,
        margin=margin,
        chunks=chunks,
    )
