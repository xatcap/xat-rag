from pathlib import Path

import pytest
from xat_rag_core.errors import ConfigError, InvalidRequestError
from xat_rag_tokenizers import MINISTRAL_3_3B, HfTokenCounter, profile

FILE = Path(__file__).resolve().parents[3] / "models" / "ministral-3-3b-tokenizer" / "tokenizer.json"


@pytest.mark.skipif(not FILE.exists(), reason="models not fetched")
def test_counts_generator_tokens() -> None:
    counter = HfTokenCounter(FILE, MINISTRAL_3_3B.tokenizer_id)
    assert counter.count("") == 0
    assert counter.count("Hello world.") == 3
    assert counter.count("NullPointerException in OrderService.java:88") > 5
    assert counter.tokenizer_id == "ministral-3-3b-tokenizer@b35d4dfe"


def test_profiles() -> None:
    assert profile("ministral-3-3b").context_window == 8192
    with pytest.raises(InvalidRequestError, match="no profile"):
        profile("gpt-x")


def test_bad_tokenizer_file(tmp_path: Path) -> None:
    bad = tmp_path / "tokenizer.json"
    bad.write_text("{}")
    with pytest.raises(ConfigError):
        HfTokenCounter(bad, "x")
