"""The generator profiles. v1 has one: `ministral-3-3b`. See ADR 0007."""

from collections.abc import Mapping
from types import MappingProxyType

from xat_rag_core.budget import GeneratorProfile
from xat_rag_core.errors import InvalidRequestError

MINISTRAL_3_3B = GeneratorProfile(
    name="ministral-3-3b",
    context_window=8192,
    tokenizer_id="ministral-3-3b-tokenizer@b35d4dfe",
    # Measured with tools/measure_profile.py and mistral-common 1.13.1 on 2026-11-08: BOS = 1 token,
    # [SYSTEM_PROMPT]...[/SYSTEM_PROMPT] and [INST]...[/INST] = 2 tokens each. Each value has 1 token of
    # margin, because the GGUF chat template of the engine can differ a little.
    template_overhead=2,
    per_message_overhead=3,
    safety_margin_pct=3.0,
)

PROFILES: Mapping[str, GeneratorProfile] = MappingProxyType({MINISTRAL_3_3B.name: MINISTRAL_3_3B})


def profile(name: str) -> GeneratorProfile:
    """Return the profile of a generator. Raise `InvalidRequestError` for a generator with no profile."""
    found = PROFILES.get(name)
    if found is None:
        raise InvalidRequestError(f"The generator '{name}' has no profile. v1 supports: {', '.join(PROFILES)}.")
    return found
