from pathlib import Path

import pytest
import xat_rag_tokenizers
from xat_rag_core.errors import ConfigError
from xat_rag_tokenizers import HfTokenCounter

FILE = Path(__file__).resolve().parents[3] / "models" / "ministral-3-3b-tokenizer" / "tokenizer.json"


@pytest.mark.skipif(not FILE.exists(), reason="models not fetched")
def test_counts_size_tokens() -> None:
    counter = HfTokenCounter(FILE, "ministral-3-3b-tokenizer@abc123")
    assert counter.count("") == 0
    assert counter.count("Hello world.") == 3
    assert counter.count("NullPointerException in OrderService.java:88") > 5
    assert counter.tokenizer_id == "ministral-3-3b-tokenizer@abc123"


def test_bad_tokenizer_file(tmp_path: Path) -> None:
    bad = tmp_path / "tokenizer.json"
    bad.write_text("{}")
    with pytest.raises(ConfigError):
        HfTokenCounter(bad, "x")


def test_the_package_exports_the_counter_only() -> None:
    assert xat_rag_tokenizers.__all__ == ["HfTokenCounter"]
    assert not hasattr(xat_rag_tokenizers, "profile")
    assert not hasattr(xat_rag_tokenizers, "PROFILES")
