import sqlite3
import threading
from pathlib import Path

import numpy as np
import pytest
import xat_rag_sqlite.store as store_module
from langchain_core.embeddings import DeterministicFakeEmbedding
from xat_rag_core.errors import IndexMismatchError, InternalError
from xat_rag_core.ids import chunk_content_hash
from xat_rag_core.model import Chunk, LineSpan, Scalar, SourceDocument, SourceKind
from xat_rag_core.query import Equals, ExactLookup, Filter, OneOf, Range
from xat_rag_sqlite.lexical import match_expression
from xat_rag_sqlite.manifest import EmbedderInfo, IndexManifest
from xat_rag_sqlite.store import SqliteRagStore, UpsertStatus

DIMS = 16
EMBED = DeterministicFakeEmbedding(size=DIMS)


def manifest(**changes: object) -> IndexManifest:
    base: dict[str, object] = {
        "rag": "jira",
        "embedder": EmbedderInfo("fake", "0" * 64, DIMS),
        "lexical_tokenizer": "code-v1",
        "generator_tokenizer_id": "tekken@test",
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
    assert [c.chunk_id for c in store.exact_lookup([ExactLookup("status", "done")], 5, Filter())] == ["jira:P-1#desc"]
    assert store.exact_lookup([ExactLookup("status", "open")], 5, Filter()) == []


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
    assert store.exact_lookup([ExactLookup("jira_key", "P-1")], 5, Filter()) == []
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


def test_exact_lookup_ranks_by_matches(store: SqliteRagStore) -> None:
    embedder = CountingEmbedder()
    upsert(
        store, document("jira:P-1", "a"), [chunk("jira:P-1", "desc", "x", jira_key="P-1", path_name="a.java")], embedder
    )
    upsert(
        store, document("jira:P-2", "b"), [chunk("jira:P-2", "desc", "y", jira_key="P-2", path_name="a.java")], embedder
    )
    hits = store.exact_lookup([ExactLookup("jira_key", "P-2"), ExactLookup("path_name", "a.java")], 5, Filter())
    assert [hit.chunk_id for hit in hits] == ["jira:P-2#desc", "jira:P-1#desc"]


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
        store.exact_lookup([ExactLookup("a", "b")], 0, Filter())


def test_failed_open_closes_the_writer(tmp_path: Path) -> None:
    path = tmp_path / "x.sqlite"
    SqliteRagStore(path, manifest()).close()
    with pytest.raises(IndexMismatchError):
        SqliteRagStore(path, manifest(index_hash="other"))
