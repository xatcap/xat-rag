"""Extra tests for the lexical tokens and the FTS5 query: unicode, long input, many terms."""

from pathlib import Path

import numpy as np
import pytest
from test_store import CountingEmbedder, chunk, document, manifest, upsert
from xat_rag_core.query import Filter
from xat_rag_sqlite.lexical import index_text, lexical_tokens, match_expression
from xat_rag_sqlite.retrievers import Fts5Retriever
from xat_rag_sqlite.store import SqliteRagStore


@pytest.fixture
def store(tmp_path: Path) -> SqliteRagStore:
    return SqliteRagStore(tmp_path / "m2.sqlite", manifest())


def _search(store: SqliteRagStore, *terms: str, k: int = 10, filters: Filter | None = None) -> list[str]:
    expression = match_expression(list(terms))
    assert expression is not None
    return [c.chunk_id for c in store.search_lexical(expression, k, filters or Filter())]


def test_diacritics_are_folded_between_index_and_query(store: SqliteRagStore) -> None:
    upsert(store, document("d:1", "a"), [chunk("d:1", "a", "Café résumé naïve")], CountingEmbedder())
    assert _search(store, "cafe") == ["d:1#a"]
    assert _search(store, "RESUME") == ["d:1#a"]
    assert _search(store, "naïve") == ["d:1#a"]
    assert _search(store, "naive") == ["d:1#a"]


def test_cjk_and_other_scripts_are_searchable(store: SqliteRagStore) -> None:
    upsert(
        store,
        document("d:1", "a"),
        [chunk("d:1", "a", "注文サービス заказ failed")],
        CountingEmbedder(),
    )
    assert _search(store, "注文サービス") == ["d:1#a"]
    assert _search(store, "ЗАКАЗ") == ["d:1#a"]


def test_unicode_tokens_are_lowercase_and_whole() -> None:
    assert lexical_tokens("Über-Service")[0] == "über_service"
    assert lexical_tokens("注文サービス") == ["注文サービス"]
    assert lexical_tokens("   \t\n") == []
    assert lexical_tokens("") == []


def test_emoji_and_punctuation_make_no_tokens() -> None:
    assert lexical_tokens("\U0001f525\U0001f525 !!! ??? ...") == []


def test_tokens_longer_than_the_limit_are_dropped() -> None:
    assert lexical_tokens("x" * 129 + " ok") == ["ok"]
    assert lexical_tokens("x" * 128) == ["x" * 128]


def test_index_text_handles_a_huge_input_fast() -> None:
    text = " ".join(f"Foo{i}.BarBaz_{i}" for i in range(20_000))
    assert len(index_text(text)) > len(text)


def test_fts_query_with_many_terms(store: SqliteRagStore) -> None:
    upsert(store, document("d:1", "a"), [chunk("d:1", "a", "needle alpha")], CountingEmbedder())
    upsert(store, document("d:2", "b"), [chunk("d:2", "a", "other beta")], CountingEmbedder())
    noise = [f"noise{i}" for i in range(60)]
    assert _search(store, *noise, "needle") == ["d:1#a"]
    assert sorted(_search(store, *noise, "needle", "beta")) == ["d:1#a", "d:2#a"]


def test_fts_operators_in_the_query_are_plain_words(store: SqliteRagStore) -> None:
    upsert(store, document("d:1", "a"), [chunk("d:1", "a", "a and not near or b")], CountingEmbedder())
    retriever = Fts5Retriever(store=store, k=5)
    assert len(retriever.search('AND OR NOT NEAR( "quote *star ^caret {brace} -dash col:on')) == 1
    assert len(retriever.search("NOT")) == 1


def test_fts_ranks_by_bm25_then_chunk_id(store: SqliteRagStore) -> None:
    embedder = CountingEmbedder()
    upsert(store, document("d:1", "a"), [chunk("d:1", "z", "timeout"), chunk("d:1", "a", "timeout")], embedder)
    upsert(store, document("d:2", "a"), [chunk("d:2", "a", "timeout timeout timeout")], embedder)
    hits = _search(store, "timeout")
    assert hits[0] == "d:2#a"
    assert hits[1:] == ["d:1#a", "d:1#z"]


def test_fts_k_limits_the_result(store: SqliteRagStore) -> None:
    embedder = CountingEmbedder()
    for i in range(5):
        upsert(store, document(f"d:{i}", "a"), [chunk(f"d:{i}", "a", "common word")], embedder)
    assert len(_search(store, "common", k=3)) == 3
    assert len(_search(store, "common", k=50)) == 5
    assert _search(store, "absent") == []


def test_search_on_an_empty_store(store: SqliteRagStore) -> None:
    assert _search(store, "anything") == []
    assert store.search_vector(np.ones(16, dtype=np.float32), 5, Filter()) == []


def test_match_expression_cap_is_kept_over_many_terms() -> None:
    expression = match_expression([f"t{i}" for i in range(500)], max_tokens=64)
    assert expression is not None
    assert len(expression.split(" OR ")) <= 64


def test_match_expression_cap_is_kept_inside_one_term() -> None:
    expression = match_expression(["a b c d e f g h"], max_tokens=3)
    assert expression is not None
    assert len(expression.split(" OR ")) == 3


def test_match_expression_removes_repeated_tokens() -> None:
    assert match_expression(["Foo", "foo", "FOO"]) == '"foo"'


def test_camel_case_split_ignores_non_ascii_letters() -> None:
    assert set(lexical_tokens("Straße")) == {"straße"}


def test_decomposed_and_composed_text_give_the_same_tokens() -> None:
    assert lexical_tokens("résumé") == lexical_tokens("résumé")
