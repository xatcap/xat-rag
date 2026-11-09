"""The chunk-boundary test (development guide, retrieval gate).

It chunks the fixture corpus and compares the chunk IDs, spans and content hashes with a saved file.
An upgrade of a parser library or of the split rules that moves a boundary fails this test.
Then check the change, write the file again with XATRAG_WRITE_BOUNDARIES=1, and note the rebuild in
the release notes, because the manifest makes the old indexes need a rebuild.

The test compares the PDF chunks only on macOS, because macOS wrote the saved file. The Docling layout
model runs on torch, and torch can group the paragraphs of a PDF differently on each operating system.
Thus the PDF chunks on Linux and Windows are different. The test compares all other chunks on each
operating system. Write the saved file only on macOS.
"""

import json
import os
import sys
from pathlib import Path

import pytest
from xat_rag.index import chunker_version, make_parser
from xat_rag.recipes import load_recipes
from xat_rag_core.model import SourceKind
from xat_rag_core.parsing import Deadline
from xat_rag_core.spans import span_to_meta
from xat_rag_eval.corpus import load_corpus
from xat_rag_onnx import OnnxModel
from xat_rag_parsers import ChunkLimits, StructuralChunker
from xat_rag_tokenizers import HfTokenCounter

ROOT = Path(__file__).resolve().parents[3]
MODELS = ROOT / "models"
SETUP = ROOT / "eval" / "fixtures" / "setup"
SAVED = ROOT / "eval" / "chunk_boundaries.json"
CHECK_PDFS = sys.platform == "darwin"
pytestmark = pytest.mark.skipif(not (MODELS / "bge-small-en-v1.5" / "model.onnx").exists(), reason="models not fetched")


def _boundaries() -> tuple[list[dict[str, object]], set[str]]:
    """Return the boundary entries and the doc IDs of the skipped PDF documents."""
    rags, _ = load_recipes(SETUP / "recipes")
    counter = HfTokenCounter(MODELS / "ministral-3-3b-tokenizer" / "tokenizer.json", "t")
    embedder = OnnxModel(MODELS / "bge-small-en-v1.5", threads=1)
    result: list[dict[str, object]] = []
    skipped: set[str] = set()
    for raw in load_corpus(SETUP / "documents"):
        if raw.source_kind is SourceKind.PDF and not CHECK_PDFS:
            skipped.add(raw.doc_id)
            continue
        recipe = rags[raw.rag]
        kind = raw.source_kind.value
        parser = make_parser(kind, recipe, None, MODELS)
        limits = ChunkLimits(recipe.source_for(kind).max_chunk_tokens, max_embed_tokens=510)
        chunks = StructuralChunker(counter.count, embedder.count, limits).chunk(
            parser.parse(raw, Deadline.none()), chunker_version(parser)
        )
        for chunk in chunks:
            entry: dict[str, object] = {"chunk_id": chunk.chunk_id, "hash": chunk.content_hash[:16]}
            entry["span"] = dict(span_to_meta(chunk.span))
            result.append(entry)
    return result, skipped


def test_chunk_boundaries_did_not_move() -> None:
    write = os.environ.get("XATRAG_WRITE_BOUNDARIES") == "1"
    assert CHECK_PDFS or not write, "Write the file only on macOS, because the PDF chunks change on other systems."
    current, skipped = _boundaries()
    if write:
        SAVED.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n")
    assert SAVED.exists(), "Write the file once with XATRAG_WRITE_BOUNDARIES=1."
    saved: list[dict[str, object]] = json.loads(SAVED.read_text())
    prefixes = tuple(f"{doc_id}#" for doc_id in skipped)
    expected = [entry for entry in saved if not (prefixes and str(entry["chunk_id"]).startswith(prefixes))]
    assert current == expected
