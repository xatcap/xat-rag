"""The chunk-boundary test (architecture, section 13).

It chunks the fixture corpus and compares the chunk IDs, spans and content hashes with a saved file.
An upgrade of a parser library or of `langchain-text-splitters` that moves a boundary fails this test.
Then check the change, write the file again with XATRAG_WRITE_BOUNDARIES=1, and note the rebuild in
the changelog, because the manifest makes the old indexes need a rebuild.
"""

import json
import os
from pathlib import Path

import pytest
from xat_rag.index import chunker_version, make_parser
from xat_rag.recipes import load_recipes
from xat_rag_core.lc_codec import span_to_meta
from xat_rag_core.parsing import Deadline
from xat_rag_eval.corpus import load_corpus
from xat_rag_onnx import OnnxModel
from xat_rag_parsers import ChunkLimits, StructuralChunker
from xat_rag_tokenizers import HfTokenCounter

ROOT = Path(__file__).resolve().parents[3]
MODELS = ROOT / "models"
SETUP = ROOT / "eval" / "fixtures" / "setup"
SAVED = ROOT / "eval" / "chunk_boundaries.json"
pytestmark = pytest.mark.skipif(not (MODELS / "bge-small-en-v1.5" / "model.onnx").exists(), reason="models not fetched")


def _boundaries() -> list[dict[str, object]]:
    rags, _ = load_recipes(SETUP / "recipes")
    counter = HfTokenCounter(MODELS / "ministral-3-3b-tokenizer" / "tokenizer.json", "t")
    embedder = OnnxModel(MODELS / "bge-small-en-v1.5", threads=1)
    result: list[dict[str, object]] = []
    for raw in load_corpus(SETUP / "documents"):
        recipe = rags[raw.rag]
        kind = raw.source_kind.value
        parser = make_parser(kind, recipe, None)
        limits = ChunkLimits(recipe.source_for(kind).max_chunk_tokens, max_embed_tokens=510)
        chunks = StructuralChunker(counter.count, embedder.count, limits).chunk(
            parser.parse(raw, Deadline.none()), chunker_version(parser)
        )
        for chunk in chunks:
            entry: dict[str, object] = {"chunk_id": chunk.chunk_id, "hash": chunk.content_hash[:16]}
            entry["span"] = dict(span_to_meta(chunk.span))
            result.append(entry)
    return result


def test_chunk_boundaries_did_not_move() -> None:
    current = _boundaries()
    if os.environ.get("XATRAG_WRITE_BOUNDARIES") == "1":
        SAVED.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n")
    assert SAVED.exists(), "Write the file once with XATRAG_WRITE_BOUNDARIES=1."
    assert current == json.loads(SAVED.read_text())
