from pathlib import Path

import numpy as np
from langchain_core.embeddings import DeterministicFakeEmbedding
from test_store import DIMS, chunk, document, manifest
from xat_rag_core import lc_codec
from xat_rag_core.query import ExactLookup, Filter
from xat_rag_sqlite.retrievers import ExactLookupRetriever, Fts5Retriever, VecRetriever
from xat_rag_sqlite.store import SqliteRagStore

EMBED = DeterministicFakeEmbedding(size=DIMS)


def _store(tmp_path: Path) -> SqliteRagStore:
    store = SqliteRagStore(tmp_path / "r.sqlite", manifest())
    for i, text in enumerate(
        ["NullPointerException in OrderService", "Timeout in PaymentClient", "Docs about nothing"]
    ):
        doc_id = f"jira:P-{i}"
        chunks = [chunk(doc_id, "desc", text, jira_key=f"P-{i}")]
        plan = store.plan_upsert(document(doc_id, str(i)), chunks)
        vectors = np.asarray(EMBED.embed_documents([c.text for c in plan.to_embed]), dtype=np.float32)
        store.apply_upsert(plan, vectors)
    return store


def test_fts5_retriever_ranks(tmp_path: Path) -> None:
    store = _store(tmp_path)
    docs = Fts5Retriever(store=store, k=5).invoke("OrderService", config={"callbacks": []})
    candidates = [lc_codec.document_to_candidate(d) for d in docs]
    assert [c.chunk_id for c in candidates] == ["jira:P-0#desc"]
    assert candidates[0].scores.lexical_rank == 1
    assert Fts5Retriever(store=store).invoke("!!!") == []


def test_vec_retriever_finds_the_same_text(tmp_path: Path) -> None:
    store = _store(tmp_path)
    docs = VecRetriever(store=store, embeddings=EMBED, k=1).invoke("Timeout in PaymentClient")
    assert lc_codec.chunk_id_of(docs[0]) == "jira:P-1#desc"
    assert lc_codec.document_to_candidate(docs[0]).scores.dense_rank == 1


def test_exact_lookup_retriever(tmp_path: Path) -> None:
    store = _store(tmp_path)
    retriever = ExactLookupRetriever(store=store, lookups=(ExactLookup("jira_key", "P-2"),), filters=Filter())
    docs = retriever.invoke("")
    assert [lc_codec.chunk_id_of(d) for d in docs] == ["jira:P-2#desc"]
    assert lc_codec.document_to_candidate(docs[0]).scores.exact_rank == 1
