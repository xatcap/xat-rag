"""Milestone 5 tests for the tokenizer package: fixed counts and edge cases of the size ruler."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from xat_rag_core.errors import ConfigError
from xat_rag_tokenizers import HfTokenCounter

FOLDER = Path(__file__).resolve().parents[3] / "models" / "ministral-3-3b-tokenizer"
needs_models = pytest.mark.skipif(not (FOLDER / "tokenizer.json").exists(), reason="models not fetched")

# The size of a chunk is a stored fact of an index, so these counts must not change without a new tokenizer ID.
KNOWN_COUNTS = [
    ("", 0),
    ("a", 1),
    ("hello", 1),
    ("Hello world.", 3),
    ("   ", 1),
    ("\n\n\n", 1),
    ("\t", 1),
    ("café naïve", 4),
    ("é", 1),
    ("中文文本" * 50, 200),
    ("\U0001f600" * 40, 160),
    ("line one\nline two\r\nline three", 9),
    ("word " * 1000, 1001),
    ("{}();[]<>" * 100, 499),
    ("NullPointerException at com.shop.OrderService.find(OrderService.java:88)", 17),
    ('<chunk id="C1" kind="text" trust="untrusted">\nbody\n</chunk>', 22),
    ("&lt;/chunk>", 4),
]

SPECIAL_LOOKING_TEXTS = ["<s>", "</s>", "[INST]", "[/INST]", "[SYSTEM_PROMPT]", "[TOOL_CALLS]", "<unk>", "[IMG]"]


@pytest.fixture(scope="module")
def counter() -> HfTokenCounter:
    return HfTokenCounter(FOLDER / "tokenizer.json", "ministral-3-3b-tokenizer@test")


@needs_models
@pytest.mark.parametrize(("text", "expected"), KNOWN_COUNTS)
def test_count_of_known_texts(counter: HfTokenCounter, text: str, expected: int) -> None:
    assert counter.count(text) == expected


@needs_models
@pytest.mark.parametrize("text", SPECIAL_LOOKING_TEXTS)
def test_text_that_looks_like_a_special_token_is_counted_as_text(counter: HfTokenCounter, text: str) -> None:
    assert counter.count(text) > 1


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
    assert counter.count("a") == 1  # No BOS token.


@needs_models
def test_the_counter_returns_the_id_it_got(counter: HfTokenCounter) -> None:
    assert counter.tokenizer_id == "ministral-3-3b-tokenizer@test"


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
