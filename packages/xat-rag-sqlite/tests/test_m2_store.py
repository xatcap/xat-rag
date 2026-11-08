"""Extra tests for the store: concurrency, rollback, secure delete, filters and search edges."""

import threading
from pathlib import Path

import numpy as np
import pytest
from test_store import DIMS, EMBED, CountingEmbedder, chunk, document, manifest, stored_field_chunks, upsert
from xat_rag_core.errors import InternalError, ServiceUnavailableError
from xat_rag_core.model import Scalar
from xat_rag_core.query import Equals, Filter, OneOf, Range
from xat_rag_sqlite.lexical import match_expression
from xat_rag_sqlite.store import SqliteRagStore, UpsertPlan, UpsertStatus


@pytest.fixture
def store(tmp_path: Path) -> SqliteRagStore:
    return SqliteRagStore(tmp_path / "m2.sqlite", manifest())


def _add(store: SqliteRagStore, doc_id: str, text: str, **metadata: Scalar) -> None:
    upsert(store, document(doc_id, text), [chunk(doc_id, "c", text, **metadata)], CountingEmbedder())


def _lexical(store: SqliteRagStore, term: str, filters: Filter | None = None) -> set[str]:
    expression = match_expression([term])
    assert expression is not None
    return {c.chunk_id for c in store.search_lexical(expression, 100, filters or Filter())}


def _vector(store: SqliteRagStore, k: int, filters: Filter | None = None) -> set[str]:
    query = np.asarray(EMBED.embed_query("probe"), dtype=np.float32)
    return {c.chunk_id for c in store.search_vector(query, k, filters or Filter())}


def _state(store: SqliteRagStore) -> tuple[tuple[int, int], set[str], set[str], set[str]]:
    return (
        store.counts(),
        _lexical(store, "alpha"),
        _vector(store, 100),
        set(stored_field_chunks(store, "tag", "t")),
    )


# Concurrency.


def test_readers_query_while_a_writer_upserts(store: SqliteRagStore) -> None:
    total = 40
    done = threading.Event()
    errors: list[BaseException] = []
    seen_counts: list[list[int]] = []

    def writer() -> None:
        try:
            embedder = CountingEmbedder()
            for i in range(total):
                doc_id = f"d:{i}"
                upsert(store, document(doc_id, "a"), [chunk(doc_id, "c", f"alpha item {i}", tag="t")], embedder)
        except BaseException as error:
            errors.append(error)
        finally:
            done.set()

    def reader() -> None:
        counts: list[int] = []
        try:
            while not done.is_set():
                counts.append(store.counts()[1])
                _lexical(store, "alpha")
                _vector(store, 5)
                _vector(store, 5, Filter((Equals("tag", "t"),)))
        except BaseException as error:
            errors.append(error)
        seen_counts.append(counts)

    threads = [threading.Thread(target=writer), *(threading.Thread(target=reader) for _ in range(3))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert store.counts() == (total, total)
    for counts in seen_counts:
        assert counts == sorted(counts)
        assert all(0 <= value <= total for value in counts)


def test_two_writers_do_not_lose_documents(store: SqliteRagStore) -> None:
    errors: list[BaseException] = []

    def write(prefix: str) -> None:
        try:
            embedder = CountingEmbedder()
            for i in range(15):
                doc_id = f"{prefix}:{i}"
                upsert(store, document(doc_id, "a"), [chunk(doc_id, "c", f"alpha {doc_id}")], embedder)
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=write, args=(prefix,)) for prefix in ("x", "y")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert errors == []
    assert store.counts() == (30, 30)


def test_closed_store_refuses_reads(store: SqliteRagStore) -> None:
    store.close()
    with pytest.raises(ServiceUnavailableError):
        store.counts()


# Rollback.


def test_wrong_vector_size_changes_nothing(store: SqliteRagStore) -> None:
    _add(store, "d:1", "alpha one", tag="t")
    before = _state(store)
    new_chunks = [chunk("d:1", "c", "alpha one", tag="t"), chunk("d:1", "n", "alpha new", tag="t")]
    plan = store.plan_upsert(document("d:1", "b"), new_chunks)
    assert len(plan.to_embed) == 1
    with pytest.raises(InternalError):
        store.apply_upsert(plan, np.zeros((1, DIMS + 1), dtype=np.float32))
    with pytest.raises(InternalError):
        store.apply_upsert(plan, np.zeros((2, DIMS), dtype=np.float32))
    assert _state(store) == before
    assert not store.chunk_exists("d:1#n")
    store.apply_upsert(plan, np.ones((1, DIMS), dtype=np.float32))
    assert store.chunk_exists("d:1#n")


def test_failure_in_the_middle_of_a_transaction_rolls_back(store: SqliteRagStore) -> None:
    _add(store, "d:1", "alpha one", tag="t")
    before = _state(store)
    old = store.get_chunks(["d:1#c"])["d:1#c"]
    new_chunk = chunk("d:1", "n", "alpha replaced", tag="t")
    good = np.ones(DIMS, dtype=np.float32).tobytes()
    bad = np.ones(DIMS + 3, dtype=np.float32).tobytes()  # The document row and the stale delete run before this fails.
    plan = UpsertPlan(
        document=document("d:1", "changed"),
        status=UpsertStatus.UPDATED,
        to_embed=(),
        to_insert_reused=((chunk("d:1", "ok", "alpha ok", tag="t"), good), (new_chunk, bad)),
        to_update=(),
        stale_ids=("d:1#c",),
    )
    with pytest.raises(Exception, match=r"(?i)dimension|size|vector"):
        store.apply_upsert(plan, np.zeros((0, DIMS), dtype=np.float32))
    assert _state(store) == before
    assert store.get_chunks(["d:1#c"])["d:1#c"] == old
    assert not store.chunk_exists("d:1#ok")
    # The writer is usable after the rollback.
    _add(store, "d:2", "alpha two", tag="t")
    assert store.counts() == (2, 2)


def test_failed_new_document_leaves_no_document_row(store: SqliteRagStore) -> None:
    bad = np.ones(DIMS + 1, dtype=np.float32).tobytes()
    plan = UpsertPlan(document("d:9", "a"), UpsertStatus.CREATED, (), ((chunk("d:9", "c", "alpha"), bad),), (), ())
    with pytest.raises(Exception, match=r"(?i)dimension|size|vector"):
        store.apply_upsert(plan, np.zeros((0, DIMS), dtype=np.float32))
    assert store.counts() == (0, 0)
    assert not store.has_document("d:9")


def test_duplicate_chunk_ids_are_refused_by_the_plan(store: SqliteRagStore) -> None:
    with pytest.raises(InternalError):
        store.plan_upsert(document("d:1", "a"), [chunk("d:1", "c", "x"), chunk("d:1", "c", "y")])


# Secure delete.


def _disk_bytes(store: SqliteRagStore) -> bytes:
    data = b""
    for suffix in ("", "-wal"):
        path = Path(f"{store.path}{suffix}")
        if path.exists():
            data += path.read_bytes()
    return data


SECRET = "zebraquartzsecret"


def test_deleted_text_is_not_left_in_the_files(store: SqliteRagStore) -> None:
    _add(store, "d:1", f"alpha {SECRET} confidential body " * 20)
    _add(store, "d:2", "alpha harmless neighbour")
    assert SECRET.encode() in _disk_bytes(store)
    assert store.delete_document("d:1")
    store.checkpoint()
    assert SECRET.encode() not in _disk_bytes(store)
    assert _lexical(store, "harmless") == {"d:2#c"}


def test_replaced_text_is_not_left_in_the_files(store: SqliteRagStore) -> None:
    _add(store, "d:1", f"alpha {SECRET} confidential body " * 20)
    assert SECRET.encode() in _disk_bytes(store)
    upsert(store, document("d:1", "new"), [chunk("d:1", "c", "alpha clean text")], CountingEmbedder())
    store.checkpoint()
    assert SECRET.encode() not in _disk_bytes(store)
    assert _lexical(store, SECRET) == set()


# Filters.


def _filter_fixture(store: SqliteRagStore) -> None:
    rows: list[tuple[str, dict[str, Scalar]]] = [
        ("d:1", {"project": "shop", "priority": 1, "open": True, "ratio": 0.5}),
        ("d:2", {"project": "shop", "priority": 3, "open": False, "ratio": 1.5}),
        ("d:3", {"project": "billing", "priority": 5, "open": True, "ratio": 2.5}),
        ("d:4", {"project": "billing", "priority": 7, "open": False}),
        ("d:5", {"priority": 9}),
    ]
    for doc_id, metadata in rows:
        _add(store, doc_id, f"alpha {doc_id}", **metadata)


def _ids(store: SqliteRagStore, filters: Filter) -> set[str]:
    return _lexical(store, "alpha", filters)


def test_one_of_with_booleans(store: SqliteRagStore) -> None:
    _filter_fixture(store)
    assert _ids(store, Filter((OneOf("open", (True,)),))) == {"d:1#c", "d:3#c"}
    assert _ids(store, Filter((OneOf("open", (False,)),))) == {"d:2#c", "d:4#c"}
    assert _ids(store, Filter((OneOf("open", (True, False)),))) == {"d:1#c", "d:2#c", "d:3#c", "d:4#c"}
    assert _ids(store, Filter((Equals("open", True),))) == {"d:1#c", "d:3#c"}


def test_range_with_only_one_bound(store: SqliteRagStore) -> None:
    _filter_fixture(store)
    assert _ids(store, Filter((Range("priority", minimum=5),))) == {"d:3#c", "d:4#c", "d:5#c"}
    assert _ids(store, Filter((Range("priority", maximum=3),))) == {"d:1#c", "d:2#c"}
    assert _ids(store, Filter((Range("priority", minimum=3, maximum=3),))) == {"d:2#c"}
    assert _ids(store, Filter((Range("ratio", minimum=1.0),))) == {"d:2#c", "d:3#c"}
    assert _ids(store, Filter((Range("priority", minimum=100),))) == set()


def test_range_on_text_values(store: SqliteRagStore) -> None:
    _filter_fixture(store)
    assert _ids(store, Filter((Range("project", minimum="c"),))) == {"d:1#c", "d:2#c"}
    assert _ids(store, Filter((Range("project", maximum="c"),))) == {"d:3#c", "d:4#c"}


def test_many_conditions_are_intersected(store: SqliteRagStore) -> None:
    _filter_fixture(store)
    both = Filter((Equals("project", "billing"), Range("priority", minimum=6)))
    assert _ids(store, both) == {"d:4#c"}
    three = Filter((OneOf("project", ("shop", "billing")), Equals("open", True), Range("priority", maximum=4)))
    assert _ids(store, three) == {"d:1#c"}
    same_field_twice = Filter((Range("priority", minimum=3), Range("priority", maximum=5)))
    assert _ids(store, same_field_twice) == {"d:2#c", "d:3#c"}
    disjoint = Filter((Equals("project", "shop"), Equals("project", "billing")))
    assert _ids(store, disjoint) == set()


def test_filter_on_a_missing_field_or_value_matches_nothing(store: SqliteRagStore) -> None:
    _filter_fixture(store)
    assert _ids(store, Filter((Equals("no_such_field", "x"),))) == set()
    assert _ids(store, Filter((Equals("project", "nothing"),))) == set()
    assert _ids(store, Filter((Equals("project", "shop"), Equals("no_such_field", 1)))) == set()


def test_filter_on_the_built_in_fields(store: SqliteRagStore) -> None:
    _filter_fixture(store)
    assert _ids(store, Filter((Equals("doc_id", "d:2"),))) == {"d:2#c"}
    assert _ids(store, Filter((Equals("source_kind", "jira"), Equals("kind", "comment")))) == {
        f"d:{i}#c" for i in range(1, 6)
    }
    assert _ids(store, Filter((Equals("kind", "other"),))) == set()


def test_filters_apply_to_the_dense_search_before_the_cut(store: SqliteRagStore) -> None:
    _filter_fixture(store)
    only_open = Filter((OneOf("open", (True,)),))
    assert _vector(store, 1, only_open) <= {"d:1#c", "d:3#c"}
    assert len(_vector(store, 1, only_open)) == 1
    assert _vector(store, 10, only_open) == {"d:1#c", "d:3#c"}
    assert _vector(store, 10, Filter((Range("priority", minimum=100),))) == set()


def test_filter_values_with_sql_text_are_plain_values(store: SqliteRagStore) -> None:
    _filter_fixture(store)
    evil = "x') OR 1=1 --"
    assert _ids(store, Filter((Equals("project", evil),))) == set()
    assert _ids(store, Filter((OneOf("project", (evil, "shop")),))) == {"d:1#c", "d:2#c"}
    assert store.counts() == (5, 5)


# Metadata fields.


def test_metadata_fields_are_stored_for_each_chunk_and_are_case_sensitive(store: SqliteRagStore) -> None:
    _add(store, "d:1", "a", jira_key="P-1", path_name="a.java")
    _add(store, "d:2", "b", jira_key="P-2", path_name="a.java")
    assert stored_field_chunks(store, "path_name", "a.java") == ["d:1#c", "d:2#c"]
    assert stored_field_chunks(store, "jira_key", "P-1") == ["d:1#c"]
    assert stored_field_chunks(store, "jira_key", "p-1") == []
    assert stored_field_chunks(store, "missing", "x") == []
    assert _ids(store, Filter((Equals("jira_key", "p-1"),))) == set()


def test_filters_on_metadata_fields_work_in_both_searches(store: SqliteRagStore) -> None:
    _add(store, "d:1", "alpha one", jira_key="P-1", project="shop", priority=1)
    _add(store, "d:2", "alpha two", jira_key="P-2", project="billing", priority=2)
    _add(store, "d:3", "alpha three", jira_key="P-3", project="shop", priority=3)
    shop = Filter((Equals("project", "shop"),))
    narrow = Filter((Equals("project", "shop"), Range("priority", minimum=2)))
    keys = Filter((OneOf("jira_key", ("P-1", "P-2")),))
    assert _lexical(store, "alpha", shop) == _vector(store, 10, shop) == {"d:1#c", "d:3#c"}
    assert _lexical(store, "alpha", narrow) == _vector(store, 10, narrow) == {"d:3#c"}
    assert _lexical(store, "alpha", keys) == _vector(store, 10, keys) == {"d:1#c", "d:2#c"}
    none = Filter((Equals("project", "none"),))
    assert _lexical(store, "alpha", none) == _vector(store, 10, none) == set()


def test_changed_metadata_leaves_no_old_value_in_the_fields_table(store: SqliteRagStore) -> None:
    _add(store, "d:1", "alpha", jira_key="P-1", old_field="x")
    embedder = CountingEmbedder()
    upsert(store, document("d:1", "alpha"), [chunk("d:1", "c", "alpha", jira_key="P-9")], embedder)
    assert stored_field_chunks(store, "jira_key", "P-1") == []
    assert stored_field_chunks(store, "jira_key", "P-9") == ["d:1#c"]
    assert stored_field_chunks(store, "old_field", "x") == []


# Dense search edges.


def test_vector_search_with_k_larger_than_the_rows(store: SqliteRagStore) -> None:
    for i in range(3):
        _add(store, f"d:{i}", f"alpha {i}")
    assert len(_vector(store, 3)) == 3
    assert len(_vector(store, 100)) == 3
    assert len(_vector(store, 1000)) == 3


def test_vector_search_orders_by_distance_then_chunk_id(store: SqliteRagStore) -> None:
    same = np.ones((1, DIMS), dtype=np.float32)
    for doc_id in ("d:b", "d:a"):
        doc = document(doc_id, "x")
        plan = store.plan_upsert(doc, [chunk(doc_id, "c", f"text {doc_id}")])
        store.apply_upsert(plan, same)
    hits = store.search_vector(np.ones(DIMS, dtype=np.float32), 5, Filter())
    assert [h.chunk_id for h in hits] == ["d:a#c", "d:b#c"]


def test_vector_search_refuses_a_wrong_query_size(store: SqliteRagStore) -> None:
    with pytest.raises(InternalError):
        store.search_vector(np.ones(DIMS - 1, dtype=np.float32), 5, Filter())


def test_vectors_come_back_in_the_requested_order(store: SqliteRagStore) -> None:
    for i in range(3):
        _add(store, f"d:{i}", f"alpha {i}")
    ids = ["d:2#c", "d:0#c", "d:1#c"]
    result = store.vectors(ids)
    expected = np.asarray(EMBED.embed_documents([f"alpha {i}" for i in (2, 0, 1)]), dtype=np.float32)
    np.testing.assert_allclose(result, expected, rtol=1e-6)
    assert store.vectors([]).shape == (0, DIMS)
    with pytest.raises(InternalError):
        store.vectors(["d:0#c", "missing#c"])


def test_get_chunks_ignores_unknown_ids_and_repeats(store: SqliteRagStore) -> None:
    _add(store, "d:1", "alpha")
    assert store.get_chunks([]) == {}
    assert set(store.get_chunks(["d:1#c", "d:1#c", "nope"])) == {"d:1#c"}


def test_a_large_batch_of_ids_is_read(store: SqliteRagStore) -> None:
    _add(store, "d:1", "alpha")
    ids = [f"missing-{i}" for i in range(500)] + ["d:1#c"]
    assert set(store.get_chunks(ids)) == {"d:1#c"}
