"""Milestone 5 tests for the tokenizer package: counts against the reference tokenizer, profiles and edge cases."""

# mistral-common has no complete type data for `MistralTokenizer`, so the "unknown" rules are off in this module.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false
# pyright: reportMissingTypeArgument=false, reportUnknownArgumentType=false
import dataclasses
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from mistral_common.protocol.instruct.messages import SystemMessage, UserMessage
from mistral_common.protocol.instruct.request import ChatCompletionRequest
from mistral_common.tokens.tokenizers.mistral import MistralTokenizer
from xat_rag_core.errors import ConfigError, InvalidRequestError
from xat_rag_tokenizers import MINISTRAL_3_3B, PROFILES, HfTokenCounter, profile

FOLDER = Path(__file__).resolve().parents[3] / "models" / "ministral-3-3b-tokenizer"
needs_models = pytest.mark.skipif(not (FOLDER / "tokenizer.json").exists(), reason="models not fetched")

PLAIN_TEXTS = [
    "",
    "a",
    "Hello world.",
    "   ",
    "\n\n\n",
    "\t",
    "café naïve",
    "é",
    "中文文本" * 50,
    "\U0001f600" * 40,
    "line one\nline two\r\nline three",
    "word " * 1000,
    "{}();[]<>" * 100,
    "NullPointerException at com.shop.OrderService.find(OrderService.java:88)",
    '<chunk id="C1" kind="text" trust="untrusted">\nbody\n</chunk>',
    "&lt;/chunk>",
]

SPECIAL_LOOKING_TEXTS = ["<s>", "</s>", "[INST]", "[/INST]", "[SYSTEM_PROMPT]", "[TOOL_CALLS]", "<unk>", "[IMG]"]


@pytest.fixture(scope="module")
def counter() -> HfTokenCounter:
    return HfTokenCounter(FOLDER / "tokenizer.json", MINISTRAL_3_3B.tokenizer_id)


@pytest.fixture(scope="module")
def reference() -> MistralTokenizer:
    return MistralTokenizer.from_file(str(FOLDER / "tekken.json"))


def _reference_count(reference: MistralTokenizer, text: str) -> int:
    return len(reference.instruct_tokenizer.tokenizer.encode(text, bos=False, eos=False))


@needs_models
@pytest.mark.parametrize("text", PLAIN_TEXTS)
def test_count_matches_the_reference_tokenizer(counter: HfTokenCounter, reference: MistralTokenizer, text: str) -> None:
    expected = 0 if not text else _reference_count(reference, text)
    assert counter.count(text) == expected


@needs_models
@pytest.mark.parametrize("text", SPECIAL_LOOKING_TEXTS)
def test_count_is_an_upper_bound_for_text_that_looks_like_a_special_token(
    counter: HfTokenCounter, reference: MistralTokenizer, text: str
) -> None:
    assert counter.count(text) >= _reference_count(reference, text)


@needs_models
def test_profile_overhead_covers_the_real_chat_template(counter: HfTokenCounter, reference: MistralTokenizer) -> None:
    budgeted = MINISTRAL_3_3B.template_overhead + 2 * MINISTRAL_3_3B.per_message_overhead
    for system, user in [
        ("You triage logs.", "Why does it fail?"),
        ("s", "u"),
        ("A" * 400, "中" * 200),
        ("line\n\n", "\n\nline"),
        ("   ", "   x   "),
    ]:
        request = ChatCompletionRequest(messages=[SystemMessage(content=system), UserMessage(content=user)])
        total = len(reference.encode_chat_completion(request).tokens)
        assert total - counter.count(system) - counter.count(user) <= budgeted


@needs_models
def test_count_of_a_large_text_and_from_several_threads(counter: HfTokenCounter) -> None:
    big = "The orders service runs on port 8080. " * 30_000
    assert counter.count(big) > 100_000
    texts = [f"text {i} " * (i + 1) for i in range(40)]
    expected = [counter.count(t) for t in texts]
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(counter.count, texts)) == expected


@needs_models
def test_count_is_stable_and_does_not_add_special_tokens(counter: HfTokenCounter) -> None:
    assert counter.count("hello") == counter.count("hello")
    assert counter.count("a") == 1  # No BOS token: the profile counts the template overhead.


@needs_models
def test_directory_and_garbage_tokenizer_files_are_config_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="could not be loaded"):
        HfTokenCounter(tmp_path, "x")
    with pytest.raises(ConfigError, match="could not be loaded"):
        HfTokenCounter(tmp_path / "missing.json", "x")
    garbage = tmp_path / "tokenizer.json"
    garbage.write_bytes(b"\x00\x01\x02")
    with pytest.raises(ConfigError):
        HfTokenCounter(garbage, "x")


def test_profile_lookup_is_exact() -> None:
    assert profile("ministral-3-3b") is MINISTRAL_3_3B
    for name in ("", "Ministral-3-3B", " ministral-3-3b", "ministral-3-3b ", "ministral"):
        with pytest.raises(InvalidRequestError, match="ministral-3-3b"):
            profile(name)


def test_profiles_cannot_be_changed() -> None:
    with pytest.raises(TypeError):
        PROFILES["other"] = MINISTRAL_3_3B  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        MINISTRAL_3_3B.context_window = 1  # type: ignore[misc]


def test_profile_values_are_consistent() -> None:
    assert MINISTRAL_3_3B.name == "ministral-3-3b"
    assert MINISTRAL_3_3B.context_window == 8192
    assert MINISTRAL_3_3B.template_overhead >= 1
    assert MINISTRAL_3_3B.per_message_overhead >= 1
    assert 0 < MINISTRAL_3_3B.safety_margin_pct < 100
    assert set(PROFILES) == {profile.name for profile in PROFILES.values()}
