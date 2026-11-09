import json
from pathlib import Path

import numpy as np
import pytest
from xat_rag_core.errors import ConfigError, StageError
from xat_rag_core.ids import chunk_content_hash
from xat_rag_core.model import Candidate, Chunk, LineSpan, SourceKind
from xat_rag_onnx import OnnxEmbeddings, OnnxModel, OnnxReranker, load_lock, verify_model

ROOT = Path(__file__).resolve().parents[3]
MODELS = ROOT / "models"
LOCK = ROOT / "build" / "models.lock.json"
pytestmark = pytest.mark.skipif(not (MODELS / "bge-small-en-v1.5" / "model.onnx").exists(), reason="models not fetched")


@pytest.fixture(scope="module")
def embedder() -> OnnxEmbeddings:
    return OnnxEmbeddings(OnnxModel(MODELS / "bge-small-en-v1.5", threads=1), "bge-small-en-v1.5", 384)


@pytest.fixture(scope="module")
def reranker() -> OnnxReranker:
    return OnnxReranker(model=OnnxModel(MODELS / "ms-marco-minilm-l6-v2", threads=1), model_id="minilm", top_n=2)


def test_lock_and_verify(tmp_path: Path) -> None:
    locks = load_lock(LOCK)
    cache = tmp_path / "cache.json"
    verify_model(MODELS / "bge-small-en-v1.5", locks["bge-small-en-v1.5"], cache)
    assert cache.exists()
    verify_model(MODELS / "bge-small-en-v1.5", locks["bge-small-en-v1.5"], cache)
    copy = tmp_path / "model"
    copy.mkdir()
    for entry in locks["bge-small-en-v1.5"].files:
        (copy / entry.path).write_bytes((MODELS / "bge-small-en-v1.5" / entry.path).read_bytes())
    (copy / "config.json").write_text(json.dumps({"changed": True}))
    with pytest.raises(ConfigError, match=r"size|sha256"):
        verify_model(copy, locks["bge-small-en-v1.5"])
    with pytest.raises(ConfigError, match="damaged"):
        load_lock(tmp_path / "missing.json")


def test_embeddings_are_normalized_and_relevant(embedder: OnnxEmbeddings) -> None:
    docs = embedder.embed_documents_np(
        ["Restart the shop service with systemctl on Linux.", "The cafeteria menu has pasta on Friday."]
    )
    assert docs.shape == (2, 384)
    assert np.allclose(np.linalg.norm(docs, axis=1), 1.0, atol=1e-5)
    query = embedder.embed_query_np("how to restart the service on linux")
    assert float(docs[0] @ query) > float(docs[1] @ query)
    assert np.array_equal(embedder.embed_documents_np(["same"]), embedder.embed_documents_np(["same"]))
    assert len(embedder.embed_query_np("x")) == 384


def test_too_long_text_is_an_error(embedder: OnnxEmbeddings) -> None:
    with pytest.raises(StageError, match="512"):
        embedder.embed_documents_np(["word " * 600])
    assert embedder.count_document_tokens("hello world") == 4


def _document(chunk_id: str, text: str) -> Candidate:
    chunk = Chunk(
        chunk_id, "d", "r", SourceKind.TEXT, "section", "h", text, LineSpan(1, 1), chunk_content_hash("h", text), "v"
    )
    return Candidate(chunk)


def test_reranker_orders_and_marks_truncation(reranker: OnnxReranker) -> None:
    documents = [
        _document("a", "The cafeteria menu has pasta."),
        _document("b", "OrderNotFoundException happens when the order id is unknown in OrderService."),
        _document("c", "word " * 700),
    ]
    result = list(reranker.rerank(documents, "Why does OrderService throw OrderNotFoundException?"))  # type: ignore[arg-type]
    assert len(result) == 2
    assert result[0].chunk_id == "b"
    scores = {d.chunk_id: d.scores for d in result}
    assert all(s.rerank_score is not None for s in scores.values())
    _, truncated = reranker.score("q", ["word " * 700, "short"])
    assert truncated == [True, False]
    long_query_scores, _ = reranker.score("q " * 300, ["x"])  # A long query is cut to 256 tokens.
    assert long_query_scores.shape == (1,)
