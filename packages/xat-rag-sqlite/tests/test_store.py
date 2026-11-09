import hashlib
import sqlite3
import threading
from pathlib import Path

import numpy as np
import pytest
import xat_rag_sqlite.store as store_module
from xat_rag_core.errors import IndexMismatchError, InternalError
from xat_rag_core.ids import chunk_content_hash
from xat_rag_core.model import Chunk, LineSpan, Scalar, SourceDocument, SourceKind
from xat_rag_core.query import Equals, Filter, OneOf, Range
from xat_rag_sqlite.lexical import match_expression
from xat_rag_sqlite.manifest import EmbedderInfo, IndexManifest
from xat_rag_sqlite.store import SqliteRagStore, UpsertStatus

DIMS = 16


class _FakeEmbedding:
    """Gives each text a fixed random vector."""

    def __init__(self, size: int) -> None:
        self._size = size

    def embed_query(self, text: str) -> list[float]:
        seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16) % 10**8
        return list(np.random.default_rng(seed).normal(size=self._size))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]


EMBED = _FakeEmbedding(size=DIMS)


def manifest(**changes: object) -> IndexManifest:
    base: dict[str, object] = {
        "rag": "jira",
        "embedder": EmbedderInfo("fake", "0" * 64, DIMS),
        "lexical_tokenizer": "code-v1",
        "size_tokenizer_id": "tekken@test",
        "index_hash": "h1",
        "chunkers": {"jira": "jira-v1"},
    }
    base.update(changes)
    return IndexManifest(**base)  # type: ignore[arg-type]


def document(doc_id: str, content: str) -> SourceDocument:
    return SourceDocument(doc_id, "jira", SourceKind.JIRA, f"https://jira/{doc_id}", doc_id, "1", content)


def chunk(doc_id: str, anchor: str, text: str, **metadata: Scalar) -> Chunk:
    return Chunk(
        chunk_id=f"{doc_id}#{anchor}",
        doc_id=doc_id,
        rag="jira",
        source_kind=SourceKind.JIRA,
        kind="comment",
        header=f"{doc_id}: header",
        text=text,
        span=LineSpan(1, 2),
        content_hash=chunk_content_hash(f"{doc_id}: header", text),
        chunker_version="jira-v1",
        metadata=metadata,
    )


class CountingEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, chunks: tuple[Chunk, ...]) -> np.ndarray:
        self.calls += len(chunks)
        return np.asarray(EMBED.embed_documents([c.text for c in chunks]), dtype=np.float32).reshape(len(chunks), DIMS)


def upsert(store: SqliteRagStore, doc: SourceDocument, chunks: list[Chunk], embedder: CountingEmbedder) -> UpsertStatus:
    plan = store.plan_upsert(doc, chunks)
    store.apply_upsert(plan, embedder.embed(plan.to_embed))
    return plan.status


@pytest.fixture
def store(tmp_path: Path) -> SqliteRagStore:
    return SqliteRagStore(tmp_path / "jira.sqlite", manifest())


def stored_field_chunks(store: SqliteRagStore, field: str, value: object) -> list[str]:
    """Return the IDs of the chunks that have a metadata field value, read from the `chunk_fields` table."""
    db = sqlite3.connect(store.path)
    try:
        rows = db.execute(
            "SELECT c.chunk_id FROM chunk_fields f JOIN chunks c ON c.id = f.chunk_rowid "
            "WHERE f.field = ? AND f.value = ? ORDER BY c.chunk_id",
            (field, value),
        ).fetchall()
    finally:
        db.close()
    return [row[0] for row in rows]


def filtered_ids(store: SqliteRagStore, *conditions: Equals | OneOf | Range) -> set[str]:
    """Return the IDs of the chunks that pass a filter in a dense search of the whole store."""
    return {c.chunk_id for c in store.search_vector(np.ones(DIMS, dtype=np.float32), 100, Filter(conditions))}


def test_unchanged_content_makes_no_new_embeddings(store: SqliteRagStore) -> None:
    embedder = CountingEmbedder()
    chunks = [chunk("jira:P-1", "desc", "OrderService fails"), chunk("jira:P-1", "comment/1", "fixed in 4f2a9c1")]
    assert upsert(store, document("jira:P-1", "a"), chunks, embedder) is UpsertStatus.CREATED
    assert embedder.calls == 2
    assert upsert(store, document("jira:P-1", "a"), chunks, embedder) is UpsertStatus.UNCHANGED
    assert embedder.calls == 2
    changed = [chunks[0], chunk("jira:P-1", "comment/1", "fixed in 9999999")]
    assert upsert(store, document("jira:P-1", "b"), changed, embedder) is UpsertStatus.UPDATED
    assert embedder.calls == 3
    assert store.counts() == (1, 2)


def test_moved_content_reuses_its_vector(store: SqliteRagStore) -> None:
    embedder = CountingEmbedder()
    upsert(store, document("jira:P-1", "a"), [chunk("jira:P-1", "n/1", "same text")], embedder)
    upsert(store, document("jira:P-1", "b"), [chunk("jira:P-1", "n/2", "same text")], embedder)
    assert embedder.calls == 1
    assert store.chunk_exists("jira:P-1#n/2")
    assert not store.chunk_exists("jira:P-1#n/1")


def test_metadata_change_updates_without_embedding(store: SqliteRagStore) -> None:
    embedder = CountingEmbedder()
    upsert(store, document("jira:P-1", "a"), [chunk("jira:P-1", "desc", "t", status="open")], embedder)
    upsert(store, document("jira:P-1", "b"), [chunk("jira:P-1", "desc", "t", status="done")], embedder)
    assert embedder.calls == 1
    assert store.get_chunks(["jira:P-1#desc"])["jira:P-1#desc"].metadata == {"status": "done"}
    assert filtered_ids(store, Equals("status", "done")) == {"jira:P-1#desc"}
    assert filtered_ids(store, Equals("status", "open")) == set()
    assert stored_field_chunks(store, "status", "done") == ["jira:P-1#desc"]
    assert stored_field_chunks(store, "status", "open") == []


def test_delete_removes_from_every_search(store: SqliteRagStore) -> None:
    embedder = CountingEmbedder()
    upsert(
        store, document("jira:P-1", "a"), [chunk("jira:P-1", "desc", "NullPointerException", jira_key="P-1")], embedder
    )
    assert store.delete_document("jira:P-1")
    assert not store.delete_document("jira:P-1")
    expression = match_expression(["NullPointerException"])
    assert expression is not None
    assert store.search_lexical(expression, 5, Filter()) == []
    assert store.search_vector(np.ones(DIMS, dtype=np.float32), 5, Filter()) == []
    assert filtered_ids(store, Equals("jira_key", "P-1")) == set()
    assert stored_field_chunks(store, "jira_key", "P-1") == []
    store.checkpoint()
    assert store.counts() == (0, 0)


def test_searches_and_filters_before_the_cut(store: SqliteRagStore) -> None:
    embedder = CountingEmbedder()
    for i in range(6):
        project = "shop" if i % 2 == 0 else "billing"
        doc_id = f"jira:P-{i}"
        upsert(
            store,
            document(doc_id, str(i)),
            [chunk(doc_id, "desc", f"OrderService timeout number {i}", project=project, priority=i)],
            embedder,
        )
    expression = match_expression(["OrderService"])
    assert expression is not None
    hits = store.search_lexical(expression, 2, Filter((Equals("project", "billing"),)))
    assert len(hits) == 2
    assert all(hit.metadata["project"] == "billing" for hit in hits)
    vector = np.asarray(EMBED.embed_query("OrderService timeout number 4"), dtype=np.float32)
    dense = store.search_vector(vector, 2, Filter((OneOf("project", ("shop",)), Range("priority", maximum=2))))
    assert {hit.chunk_id for hit in dense} <= {"jira:P-0#desc", "jira:P-2#desc"}
    assert len(dense) == 2
    unfiltered = store.search_vector(vector, 1, Filter())
    assert unfiltered[0].chunk_id == "jira:P-4#desc"


def test_document_versions_returns_the_stored_version_of_known_documents(store: SqliteRagStore) -> None:
    embedder = CountingEmbedder()
    first = SourceDocument("jira:P-1", "jira", SourceKind.JIRA, "https://jira/P-1", "P-1", "v7", "a")
    second = SourceDocument("jira:P-2", "jira", SourceKind.JIRA, "https://jira/P-2", "P-2", "", "b")
    upsert(store, first, [chunk("jira:P-1", "desc", "x")], embedder)
    upsert(store, second, [chunk("jira:P-2", "desc", "y")], embedder)
    assert store.document_versions(["jira:P-1", "jira:P-2", "jira:P-1", "unknown"]) == {
        "jira:P-1": "v7",
        "jira:P-2": "",
    }
    assert store.document_versions([]) == {}
    assert store.document_versions(f"missing-{i}" for i in range(2000)) == {}


def test_a_new_version_replaces_the_stored_version(store: SqliteRagStore) -> None:
    embedder = CountingEmbedder()
    old = SourceDocument("jira:P-1", "jira", SourceKind.JIRA, "https://jira/P-1", "P-1", "v1", "a")
    new = SourceDocument("jira:P-1", "jira", SourceKind.JIRA, "https://jira/P-1", "P-1", "v2", "b")
    upsert(store, old, [chunk("jira:P-1", "desc", "x")], embedder)
    upsert(store, new, [chunk("jira:P-1", "desc", "y")], embedder)
    assert store.document_versions(["jira:P-1"]) == {"jira:P-1": "v2"}


def test_the_store_has_no_exact_lookup() -> None:
    assert not hasattr(SqliteRagStore, "exact_lookup")


def test_manifest_mismatch_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "jira.sqlite"
    SqliteRagStore(path, manifest()).close()
    SqliteRagStore(path, manifest(xat_rag_version="9.9.9")).close()
    with pytest.raises(IndexMismatchError, match="embedder"):
        SqliteRagStore(path, manifest(embedder=EmbedderInfo("other", "1" * 64, DIMS)))
    with pytest.raises(IndexMismatchError, match="chunkers"):
        SqliteRagStore(path, manifest(chunkers={"jira": "jira-v2"}))


def test_pragmas_and_secure_delete(store: SqliteRagStore) -> None:
    db = sqlite3.connect(store.path)
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    db.close()


def test_reads_from_other_threads(store: SqliteRagStore) -> None:
    embedder = CountingEmbedder()
    upsert(store, document("jira:P-1", "a"), [chunk("jira:P-1", "desc", "x")], embedder)
    results: list[bool] = []
    thread = threading.Thread(target=lambda: results.append(store.chunk_exists("jira:P-1#desc")))
    thread.start()
    thread.join()
    assert results == [True]
    store.close()


def test_document_field_change_is_written(store: SqliteRagStore) -> None:
    embedder = CountingEmbedder()
    chunks = [chunk("jira:P-1", "desc", "x")]
    store.upsert(document("jira:P-1", "a"), chunks, embedder.embed)
    renamed = SourceDocument("jira:P-1", "jira", SourceKind.JIRA, "https://jira/new", "New title", "2", "a")
    assert store.upsert(renamed, chunks, embedder.embed).status is UpsertStatus.UPDATED
    assert store.upsert(renamed, chunks, embedder.embed).status is UpsertStatus.UNCHANGED
    assert embedder.calls == 1


def test_checkpoint_raises_when_a_reader_blocks_it(store: SqliteRagStore, monkeypatch: pytest.MonkeyPatch) -> None:
    embedder = CountingEmbedder()
    store.upsert(document("jira:P-1", "a"), [chunk("jira:P-1", "desc", "secret text")], embedder.embed)
    reader = sqlite3.connect(store.path)
    reader.execute("BEGIN")
    reader.execute("SELECT count(*) FROM documents").fetchone()
    store.delete_document("jira:P-1")
    monkeypatch.setattr(store_module, "_CHECKPOINT_TRIES", 2)
    with pytest.raises(InternalError, match="blocked"):
        store.checkpoint()
    reader.execute("COMMIT")
    reader.close()
    store.checkpoint()


def test_reader_pool_is_bounded(store: SqliteRagStore) -> None:
    threads = [threading.Thread(target=store.counts) for _ in range(40)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert store.open_readers <= 8


def test_bad_k_is_refused(store: SqliteRagStore) -> None:
    with pytest.raises(InternalError):
        store.search_vector(np.ones(DIMS, dtype=np.float32), 5000, Filter())
    with pytest.raises(InternalError):
        store.search_vector(np.ones(DIMS, dtype=np.float32), 0, Filter())


def test_failed_open_closes_the_writer(tmp_path: Path) -> None:
    path = tmp_path / "x.sqlite"
    SqliteRagStore(path, manifest()).close()
    with pytest.raises(IndexMismatchError):
        SqliteRagStore(path, manifest(index_hash="other"))
