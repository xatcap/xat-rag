from pathlib import Path

import numpy as np
import xat_rag_sqlite.retrievers as retrievers_module
from langchain_core.embeddings import DeterministicFakeEmbedding
from test_store import DIMS, chunk, document, manifest
from xat_rag_core import lc_codec
from xat_rag_core.query import Equals, Filter
from xat_rag_sqlite.retrievers import Fts5Retriever, VecRetriever
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


def test_retrievers_apply_the_filters_before_the_cut(tmp_path: Path) -> None:
    store = _store(tmp_path)
    only_p1 = Filter((Equals("jira_key", "P-1"),))
    lexical = Fts5Retriever(store=store, k=5, filters=only_p1).invoke("OrderService PaymentClient")
    dense = VecRetriever(store=store, embeddings=EMBED, k=5, filters=only_p1).invoke("Docs about nothing")
    assert [lc_codec.chunk_id_of(d) for d in lexical] == ["jira:P-1#desc"]
    assert [lc_codec.chunk_id_of(d) for d in dense] == ["jira:P-1#desc"]
    assert lc_codec.document_to_candidate(dense[0]).scores.dense_rank == 1


def test_there_is_no_exact_lookup_retriever() -> None:
    assert not hasattr(retrievers_module, "ExactLookupRetriever")
