import json
from pathlib import Path

import pytest
from xat_rag import ContextRequest, Settings, XatRag
from xat_rag_core.errors import ConfigError, IndexMismatchError, NotFoundError
from xat_rag_core.model import RawDocument, SourceKind

ROOT = Path(__file__).resolve().parents[3]
MODELS = ROOT / "models"
pytestmark = pytest.mark.skipif(not (MODELS / "bge-small-en-v1.5" / "model.onnx").exists(), reason="models not fetched")

RAG_RECIPE = """
[rag]
name = "{name}"
embedder = "bge-small-en-v1.5"
reranker = "ms-marco-minilm-l6-v2"

[sources.main]
kind = "{kind}"
max_chunk_tokens = {max_tokens}

[retrieval]
keep = 4
"""

CONTEXT = """
[context]
name = "triage"
chunks = 6

[[context.sources]]
rag = "docs"

[[context.sources]]
rag = "jira"

[[context.sources]]
rag = "diffs"
"""

ASK = """
[context]
name = "ask"

[[context.sources]]
rag = "docs"
"""

DOCS = b"""# Handbook

## Orders service

The orders service runs on port 8080. When OrderNotFoundException appears, check that the order id
exists in the database, and look at the recent change to OrderService.find.

## Cafeteria

The cafeteria serves pasta on Fridays.
"""

ISSUE = {
    "key": "PROJ-123",
    "fields": {
        "summary": "OrderNotFoundException when order id has a prefix",
        "description": "Orders with the prefix X- are not found. Fixed by trimming the id in OrderService.find.",
        "status": {"name": "Done"},
        "resolution": {"name": "Fixed"},
        "comment": {"comments": [{"id": "10023", "body": "Fix is in commit 4f2a9c1."}]},
    },
}

DIFF = b"""--- a/src/OrderService.java
+++ b/src/OrderService.java
@@ -40,2 +40,2 @@ public Order find(String id) {
-    return repository.get(id);
+    return repository.get(id.trim());
     }
"""

QUESTION = "OrderNotFoundException: order X-9f1c not found in OrderService.find"


def write_setup(folder: Path, docs_max: int = 350) -> Settings:
    (folder / "recipes" / "rags").mkdir(parents=True)
    (folder / "recipes" / "contexts").mkdir(parents=True)
    for name, kind, size in (("docs", "markdown", docs_max), ("jira", "jira", 350), ("diffs", "diff", 350)):
        recipe = RAG_RECIPE.format(name=name, kind=kind, max_tokens=size)
        (folder / "recipes" / "rags" / f"{name}.toml").write_text(recipe)
    (folder / "recipes" / "contexts" / "triage.toml").write_text(CONTEXT)
    (folder / "recipes" / "contexts" / "ask.toml").write_text(ASK)
    return Settings(
        data_folder=folder / "data",
        recipes_folder=folder / "recipes",
        models_folder=MODELS,
        lock_file=ROOT / "build" / "models.lock.json",
        threads=1,
    )


@pytest.fixture(scope="module")
def setup(tmp_path_factory: pytest.TempPathFactory) -> tuple[XatRag, Path]:
    folder = tmp_path_factory.mktemp("xat")
    rag = XatRag.open(write_setup(folder))
    results = rag.ingest(
        [
            RawDocument("docs:handbook.md", "docs", SourceKind.MARKDOWN, DOCS, uri="docs/handbook.md"),
            RawDocument(
                "jira:PROJ-123", "jira", SourceKind.JIRA, json.dumps(ISSUE).encode(), uri="https://jira/PROJ-123"
            ),
            RawDocument("git:shop@4f2a9c1", "diffs", SourceKind.DIFF, DIFF, metadata={"repo": "shop", "sha": "4f2a"}),
        ]
    )
    assert [r.status for r in results] == ["created", "created", "created"], results
    return rag, folder


def test_triage_returns_chunks_of_all_rags_with_sources(setup: tuple[XatRag, Path]) -> None:
    rag, _ = setup
    result = rag.retrieve(ContextRequest("triage", question=QUESTION, debug=True))
    assert 1 < len(result.chunks) <= 6
    assert {chunk.source.rag for chunk in result.chunks} == {"docs", "jira", "diffs"}
    assert result.chunks[0].chunk_id != "docs:handbook.md#h/handbook/cafeteria"
    assert any(chunk.chunk_id.startswith("jira:PROJ-123#") for chunk in result.chunks)
    jira = next(chunk for chunk in result.chunks if chunk.source.rag == "jira")
    assert jira.source.uri == "https://jira/PROJ-123"
    assert jira.source.doc_id == "jira:PROJ-123"
    diff = next(chunk for chunk in result.chunks if chunk.source.rag == "diffs")
    assert diff.metadata["repo"] == "shop"
    assert result.debug is not None
    assert all(item.rrf_score is not None for item in result.debug)


def test_text_context_and_errors(setup: tuple[XatRag, Path]) -> None:
    rag, _ = setup
    result = rag.retrieve(ContextRequest("ask", question="Which port does the orders service use?"))
    assert result.chunks[0].chunk_id == "docs:handbook.md#h/handbook/orders-service"
    assert result.chunks[0].source.uri == "docs/handbook.md"
    with pytest.raises(NotFoundError):
        rag.retrieve(ContextRequest("missing", question="x"))


def test_unchanged_reingest_and_delete(setup: tuple[XatRag, Path]) -> None:
    rag, _ = setup
    again = rag.ingest([RawDocument("docs:handbook.md", "docs", SourceKind.MARKDOWN, DOCS, uri="docs/handbook.md")])
    assert again[0].status == "unchanged"
    assert again[0].embedded == 0
    rag.ingest([RawDocument("docs:tmp.md", "docs", SourceKind.MARKDOWN, b"# Temp\n\nTemporary note.")])
    assert rag.delete("docs", "docs:tmp.md")
    assert not rag.index("docs").store.has_document("docs:tmp.md")
    bad = rag.ingest([RawDocument("docs:bad", "docs", SourceKind.MARKDOWN, b"\xff\xfe")])
    assert bad[0].status == "failed"
    assert bad[0].reason == "invalid"


def test_recipe_change_needs_a_rebuild(tmp_path: Path) -> None:
    settings = write_setup(tmp_path)
    XatRag.open(settings).close()
    recipe = RAG_RECIPE.format(name="docs", kind="markdown", max_tokens=200)
    (tmp_path / "recipes" / "rags" / "docs.toml").write_text(recipe)
    rag = XatRag.open(settings)
    assert "docs" in rag.needs_rebuild()
    with pytest.raises(IndexMismatchError, match="index_hash"):
        rag.index("docs")
    rag.rebuild("docs")
    assert rag.needs_rebuild() == {}
    assert rag.index("docs").store.counts() == (0, 0)
    rag.close()


def test_bad_recipe_is_refused(tmp_path: Path) -> None:
    settings = write_setup(tmp_path)
    (tmp_path / "recipes" / "rags" / "docs.toml").write_text('[rag]\nname = "docs"\nembedder = "x"\nunknown = 1\n')
    with pytest.raises(ConfigError, match="unknown"):
        XatRag.open(settings)
