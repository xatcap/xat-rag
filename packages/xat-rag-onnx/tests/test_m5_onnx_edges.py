"""Milestone 5 tests for the ONNX package: the model lock, the model session, the embedder and the reranker."""

import json
import os
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import numpy as np
import pytest
from numpy.typing import NDArray
from xat_rag_core.errors import ConfigError, StageError
from xat_rag_core.ids import chunk_content_hash
from xat_rag_core.model import Candidate, Chunk, LineSpan, SourceKind
from xat_rag_onnx import (
    ModelFile,
    ModelLock,
    OnnxEmbeddings,
    OnnxModel,
    OnnxReranker,
    load_lock,
    verify_model,
)
from xat_rag_onnx.embeddings import BGE_QUERY_PREFIX
from xat_rag_onnx.session import MAX_LENGTH

ROOT = Path(__file__).resolve().parents[3]
MODELS = ROOT / "models"
HAS_MODELS = (MODELS / "bge-small-en-v1.5" / "model.onnx").exists()
needs_models = pytest.mark.skipif(not HAS_MODELS, reason="models not fetched")


# The model lock and the check of model files. These tests use small synthetic files, so they are fast.


def _lock_for(folder: Path, files: dict[str, bytes]) -> ModelLock:
    import hashlib  # noqa: PLC0415

    entries: list[ModelFile] = []
    for name, data in files.items():
        (folder / name).write_bytes(data)
        entries.append(ModelFile(name, len(data), hashlib.sha256(data).hexdigest()))
    return ModelLock("m", "embedder", "org/m", "rev", "mit", tuple(entries))


def test_lock_file_lookup_of_an_unknown_path_is_an_error(tmp_path: Path) -> None:
    lock = _lock_for(tmp_path, {"a.bin": b"abc"})
    assert lock.file("a.bin").size == 3
    with pytest.raises(ConfigError, match=r"no locked file 'b\.bin'"):
        lock.file("b.bin")


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '{"models": 5}',
        '{"models": [{"id": "m"}]}',
        json.dumps(
            {
                "models": [
                    {"id": "m", "role": "r", "repo": "x", "revision": "1", "license": "mit", "files": [{"path": "a"}]}
                ]
            }
        ),
    ],
)
def test_load_lock_refuses_a_damaged_lock(tmp_path: Path, content: str) -> None:
    path = tmp_path / "lock.json"
    path.write_text(content)
    with pytest.raises(ConfigError, match="missing or damaged"):
        load_lock(path)


def test_load_lock_says_what_to_do_for_a_missing_lock(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Put the model files and the lock file in the models folder"):
        load_lock(tmp_path / "lock.json")


def test_load_lock_reads_a_valid_lock(tmp_path: Path) -> None:
    path = tmp_path / "lock.json"
    entry = {
        "id": "m",
        "role": "embedder",
        "repo": "org/m",
        "revision": "abc",
        "license": "mit",
        "files": [{"path": "a.bin", "size": 3, "sha256": "00"}],
    }
    path.write_text(json.dumps({"models": [entry]}))
    lock = load_lock(path)["m"]
    assert (lock.role, lock.revision, lock.license) == ("embedder", "abc", "mit")
    assert lock.file("a.bin") == ModelFile("a.bin", 3, "00")


def test_verify_model_reports_a_missing_file(tmp_path: Path) -> None:
    lock = _lock_for(tmp_path, {"a.bin": b"abc"})
    (tmp_path / "a.bin").unlink()
    with pytest.raises(ConfigError, match="is missing"):
        verify_model(tmp_path, lock)


def test_verify_model_detects_changed_content_of_the_same_size(tmp_path: Path) -> None:
    lock = _lock_for(tmp_path, {"a.bin": b"abc"})
    (tmp_path / "a.bin").write_bytes(b"abd")
    with pytest.raises(ConfigError, match="sha256"):
        verify_model(tmp_path, lock)


def test_verify_model_cache_does_not_hide_a_changed_file(tmp_path: Path) -> None:
    lock = _lock_for(tmp_path, {"a.bin": b"abc"})
    cache = tmp_path / "cache" / "check.json"
    verify_model(tmp_path, lock, cache)
    assert cache.exists()
    path = tmp_path / "a.bin"
    stat = path.stat()
    path.write_bytes(b"abd")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
    with pytest.raises(ConfigError, match="sha256"):
        verify_model(tmp_path, lock, cache)


def test_verify_model_cache_skips_hashing_for_an_unchanged_file(tmp_path: Path) -> None:
    lock = _lock_for(tmp_path, {"a.bin": b"abc"})
    cache = tmp_path / "check.json"
    verify_model(tmp_path, lock, cache)
    before = cache.read_text()
    verify_model(tmp_path, lock, cache)
    assert cache.read_text() == before
    assert not cache.with_suffix(".tmp").exists()


def test_verify_model_ignores_a_damaged_cache(tmp_path: Path) -> None:
    lock = _lock_for(tmp_path, {"a.bin": b"abc"})
    cache = tmp_path / "check.json"
    cache.write_text("{ not json")
    verify_model(tmp_path, lock, cache)
    assert json.loads(cache.read_text())  # The damaged cache was replaced by a valid one.


def test_verify_model_only_checks_the_named_files(tmp_path: Path) -> None:
    lock = _lock_for(tmp_path, {"a.bin": b"abc", "b.bin": b"def"})
    (tmp_path / "b.bin").write_bytes(b"xyz")
    verify_model(tmp_path, lock, only=frozenset({"a.bin"}))
    verify_model(tmp_path, lock, only=frozenset({"not-locked.bin"}))
    with pytest.raises(ConfigError, match="sha256"):
        verify_model(tmp_path, lock, only=frozenset({"a.bin", "b.bin"}))
    with pytest.raises(ConfigError, match="sha256"):
        verify_model(tmp_path, lock)


def test_verify_model_wrong_size_does_not_need_hashing(tmp_path: Path) -> None:
    lock = _lock_for(tmp_path, {"a.bin": b"abc"})
    (tmp_path / "a.bin").write_bytes(b"abcd")
    with pytest.raises(ConfigError, match="wrong size"):
        verify_model(tmp_path, lock)


# The model session.


def test_model_from_an_empty_folder_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="could not be loaded"):
        OnnxModel(tmp_path)


@pytest.fixture(scope="module")
def bge() -> OnnxModel:
    return OnnxModel(MODELS / "bge-small-en-v1.5", threads=1)


@pytest.fixture(scope="module")
def embedder(bge: OnnxModel) -> OnnxEmbeddings:
    return OnnxEmbeddings(bge, "bge-small-en-v1.5", 384)


@pytest.fixture(scope="module")
def minilm() -> OnnxModel:
    return OnnxModel(MODELS / "ms-marco-minilm-l6-v2", threads=1)


@needs_models
def test_run_refuses_an_empty_batch_and_an_unknown_output(bge: OnnxModel) -> None:
    with pytest.raises(StageError, match="at least one text"):
        bge.run([], "last_hidden_state")
    with pytest.raises(StageError, match="failed to run"):
        bge.run([bge.encode("hello")], "no_such_output")


@needs_models
def test_special_tokens_counts_and_truncation(bge: OnnxModel) -> None:
    assert bge.count("hello world") == bge.count_plain("hello world") + 2
    pair = bge.encode_truncated("short query", "word " * 900)
    assert len(pair.ids) == MAX_LENGTH
    whole = bge.encode("short query", "word " * 900)
    assert len(whole.ids) > MAX_LENGTH  # `encode` cuts nothing.
    assert pair.ids[: len(bge.encode("short query").ids) - 1] == whole.ids[: len(bge.encode("short query").ids) - 1]


# The embedder.


@needs_models
def test_embedding_no_texts_gives_an_empty_matrix(embedder: OnnxEmbeddings) -> None:
    result = embedder.embed_documents_np([])
    assert result.shape == (0, 384)
    assert result.dtype == np.float32
    assert embedder.embed_documents_np([]).shape == (0, 384)


@needs_models
def test_embedding_is_close_for_any_batch_composition(bge: OnnxModel) -> None:
    # The locked models are int8 models. Their activation scales depend on the whole batch, so the numbers
    # differ a little between a batch and a single text. The cosine is about 0.98 to 0.999 and changes with the CPU.
    # The limit is above the 0.95 near-duplicate threshold (DEFAULT_NEAR_DUPLICATE in mmr.py).
    texts = ["short", "a much longer text " * 30, "mid sized text about the orders service", "x"]
    single = OnnxEmbeddings(bge, "bge-small-en-v1.5", 384, batch_size=1).embed_documents_np(texts)
    batched = OnnxEmbeddings(bge, "bge-small-en-v1.5", 384, batch_size=32).embed_documents_np(texts)
    alone = np.stack([OnnxEmbeddings(bge, "bge-small-en-v1.5", 384).embed_documents_np([t])[0] for t in texts])
    assert np.array_equal(single, alone)  # Same batch composition, same bits.
    assert (np.sum(single * batched, axis=1) > 0.97).all()


@needs_models
def test_query_prefix_and_document_prefix(bge: OnnxModel, embedder: OnnxEmbeddings) -> None:
    query = embedder.embed_query_np("restart the service")
    manual = embedder.embed_documents_np([BGE_QUERY_PREFIX + "restart the service"])[0]
    assert np.allclose(query, manual, atol=1e-6)
    plain = embedder.embed_documents_np(["restart the service"])[0]
    assert not np.allclose(query, plain, atol=1e-3)
    prefixed = OnnxEmbeddings(bge, "bge-small-en-v1.5", 384, document_prefix="passage: ")
    assert prefixed.count_document_tokens("x") == embedder.count_document_tokens("passage: x")
    assert np.allclose(prefixed.embed_documents_np(["x"]), embedder.embed_documents_np(["passage: x"]), atol=1e-6)


@needs_models
def test_the_limit_of_512_model_tokens_is_exact_for_documents(embedder: OnnxEmbeddings) -> None:
    fits = "word " * 510
    assert embedder.count_document_tokens(fits) == MAX_LENGTH
    assert embedder.embed_documents_np([fits]).shape == (1, 384)
    with pytest.raises(StageError, match="512"):
        embedder.embed_documents_np(["word " * 511])


@needs_models
def test_a_long_query_is_cut_and_not_refused(bge: OnnxModel, embedder: OnnxEmbeddings) -> None:
    room = MAX_LENGTH - 2 - bge.count_plain(BGE_QUERY_PREFIX)
    assert np.array_equal(embedder.embed_query_np("word " * 5000), embedder.embed_query_np("word " * room))
    assert np.array_equal(embedder.embed_query_np("\u4e2d" * 3000), embedder.embed_query_np("\u4e2d" * room))
    exact = embedder.embed_query_np("word " * room)
    assert exact.shape == (384,)
    assert np.isclose(np.linalg.norm(exact), 1.0, atol=1e-5)
    assert len(embedder.embed_query_np("{}();" * 2000)) == 384


@needs_models
def test_truncate_cuts_at_a_token_boundary(bge: OnnxModel) -> None:
    text = "hello world " * 100
    cut = bge.truncate(text, 10)
    assert text.startswith(cut)
    assert bge.count_plain(cut) == 10
    assert bge.truncate("short text", 10) == "short text"
    assert bge.truncate("word " * 10, 10) == "word " * 10
    assert bge.truncate("a b c", 0) == ""
    assert bge.count_plain(bge.truncate("\u4e2d\u6587" * 400, 20)) <= 20


@needs_models
def test_one_long_text_in_a_batch_refuses_the_whole_batch(embedder: OnnxEmbeddings) -> None:
    with pytest.raises(StageError, match="512"):
        embedder.embed_documents_np(["fine", "word " * 600, "fine"])


@needs_models
def test_wrong_declared_dimension_is_an_error(bge: OnnxModel) -> None:
    with pytest.raises(StageError, match="384 dimensions, not 100"):
        OnnxEmbeddings(bge, "bge-small-en-v1.5", 100).embed_documents_np(["x"])


@needs_models
def test_embedding_empty_and_odd_texts_are_normalized(embedder: OnnxEmbeddings) -> None:
    rows = embedder.embed_documents_np(["", "   ", "中文 text", "emoji \U0001f600", "tab\tand\nnewline"])
    assert rows.shape == (5, 384)
    assert np.isfinite(rows).all()
    assert np.allclose(np.linalg.norm(rows, axis=1), 1.0, atol=1e-5)


@needs_models
def test_embedder_is_safe_for_use_from_several_threads(embedder: OnnxEmbeddings) -> None:
    texts = [f"text number {i} about topic {i % 3}" for i in range(24)]
    expected = np.stack([embedder.embed_documents_np([t])[0] for t in texts])

    def embed_one(text: str) -> NDArray[np.float32]:
        return embedder.embed_documents_np([text])[0]

    with ThreadPoolExecutor(max_workers=4) as pool:
        parts = list(pool.map(embed_one, texts))
    assert np.array_equal(np.stack(parts), expected)


# The reranker.


def _document(chunk_id: str, text: str, header: str = "h") -> Candidate:
    chunk = Chunk(
        chunk_id,
        "d",
        "r",
        SourceKind.TEXT,
        "section",
        header,
        text,
        LineSpan(1, 1),
        chunk_content_hash(header, text),
        "v",
    )
    return Candidate(chunk)


def _ids(documents: Sequence[Candidate]) -> list[str]:
    return [d.chunk_id for d in documents]


@needs_models
def test_rerank_of_no_documents_and_no_passages(minilm: OnnxModel) -> None:
    reranker = OnnxReranker(model=minilm, model_id="minilm")
    assert list(reranker.rerank([], "query")) == []
    scores, truncated = reranker.score("query", [])
    assert scores.shape == (0,)
    assert truncated == []


@needs_models
def test_rerank_breaks_ties_by_chunk_id(minilm: OnnxModel) -> None:
    reranker = OnnxReranker(model=minilm, model_id="minilm", top_n=10)
    documents = [_document(chunk_id, "The orders service uses port 8080.") for chunk_id in ("c", "a", "b")]
    result = list(reranker.rerank(documents, "which port"))
    assert _ids(result) == ["a", "b", "c"]
    assert len({d.scores.rerank_score for d in result}) == 1


@needs_models
def test_rerank_does_not_depend_on_the_input_order(minilm: OnnxModel) -> None:
    reranker = OnnxReranker(model=minilm, model_id="minilm", top_n=10)
    documents = [
        _document("a", "The cafeteria serves pasta on Fridays."),
        _document("b", "Port 8080 is the port of the orders service."),
        _document("c", "Restart the service with systemctl."),
        _document("d", "The weather is nice."),
    ]
    forward = _ids(list(reranker.rerank(documents, "which port does orders use")))
    backward = _ids(list(reranker.rerank(documents[::-1], "which port does orders use")))
    assert forward == backward
    assert forward[0] == "b"


@needs_models
def test_rerank_top_n_larger_than_the_input_and_batches(minilm: OnnxModel) -> None:
    documents = [_document(f"d{i:02d}", f"Passage number {i} about the orders service.") for i in range(7)]
    small_batches = OnnxReranker(model=minilm, model_id="minilm", top_n=50, batch_size=2)
    large_batches = OnnxReranker(model=minilm, model_id="minilm", top_n=50, batch_size=32)
    one = list(small_batches.rerank(documents, "orders service"))
    two = list(large_batches.rerank(documents, "orders service"))
    assert len(one) == 7
    assert sorted(_ids(one)) == sorted(_ids(two)) == [f"d{i:02d}" for i in range(7)]
    # The int8 model gives scores that differ a little between batch sizes, so only near ties can change order.
    first = {d.chunk_id: d.scores.rerank_score or 0.0 for d in one}
    second = {d.chunk_id: d.scores.rerank_score or 0.0 for d in two}
    assert all(abs(first[key] - second[key]) < 0.5 for key in first)


@needs_models
def test_rerank_marks_only_the_cut_passage(minilm: OnnxModel) -> None:
    reranker = OnnxReranker(model=minilm, model_id="minilm", top_n=10)
    documents = [_document("long", "word " * 700), _document("short", "A short passage.")]
    flags = {d.chunk_id: d.scores.rerank_truncated for d in reranker.rerank(documents, "a query")}
    assert flags == {"long": True, "short": False}


@needs_models
def test_rerank_query_is_cut_to_256_tokens_and_not_refused(minilm: OnnxModel) -> None:
    reranker = OnnxReranker(model=minilm, model_id="minilm")
    assert minilm.count_plain("word " * 256) == 256
    exact, _ = reranker.score("word " * 256, ["a passage"])
    longer, _ = reranker.score("word " * 5000, ["a passage"])
    assert exact.shape == longer.shape == (1,)
    assert np.array_equal(exact, longer)


@needs_models
def test_rerank_header_is_part_of_the_scored_text(minilm: OnnxModel) -> None:
    reranker = OnnxReranker(model=minilm, model_id="minilm", top_n=2)
    documents = [
        _document("a", "See the table.", header="Orders service port"),
        _document("b", "See the table.", header="Cafeteria menu"),
    ]
    assert _ids(list(reranker.rerank(documents, "orders service port"))) == ["a", "b"]


def test_reranker_settings_are_validated(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="top_n"):
        OnnxReranker(model=cast(OnnxModel, object()), model_id="m", top_n=0)
