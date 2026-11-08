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
name = "log-triage"
generator = "ministral-3-3b"
window = 8192
reserved_output = 768
input_max_tokens = 1200
pinned = ["prompt:log-triage-system@3"]
query_builder = "log-v1"

[[context.sources]]
rag = "docs"
min_tokens = 200
max_share = 0.5

[[context.sources]]
rag = "jira"
min_tokens = 200
max_share = 0.5

[[context.sources]]
rag = "diffs"
min_tokens = 200
max_share = 0.5
"""

ASK = """
[context]
name = "ask"
generator = "ministral-3-3b"
query_builder = "text-v1"

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

LOGS = (
    "2026-11-07T10:00:01.123Z INFO  [http] GET /api/orders/42 200 in 12 ms",
    "2026-11-07T10:00:03.001Z ERROR [orders] com.shop.OrderNotFoundException: order X-9f1c not found",
    "    at com.shop.OrderService.find(OrderService.java:41)",
)


def write_setup(folder: Path, docs_max: int = 350) -> Settings:
    (folder / "recipes" / "rags").mkdir(parents=True)
    (folder / "recipes" / "contexts").mkdir(parents=True)
    for name, kind, size in (("docs", "markdown", docs_max), ("jira", "jira", 350), ("diffs", "diff", 350)):
        recipe = RAG_RECIPE.format(name=name, kind=kind, max_tokens=size)
        (folder / "recipes" / "rags" / f"{name}.toml").write_text(recipe)
    (folder / "recipes" / "contexts" / "log-triage.toml").write_text(CONTEXT)
    (folder / "recipes" / "contexts" / "ask.toml").write_text(ASK)
    (folder / "prompts").mkdir()
    (folder / "prompts" / "log-triage-system@3.md").write_text("You help a developer find the cause of log errors.")
    return Settings(
        data_folder=folder / "data",
        recipes_folder=folder / "recipes",
        models_folder=MODELS,
        lock_file=ROOT / "build" / "models.lock.json",
        prompts_folder=folder / "prompts",
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


def test_log_triage_pack(setup: tuple[XatRag, Path]) -> None:
    rag, _ = setup
    pack = rag.compose(ContextRequest("log-triage", log_lines=LOGS, debug=True))
    assert pack.generator == "ministral-3-3b"
    assert pack.max_tokens == 768
    used = pack.budget
    parts = (used.reserved_output, used.overhead, used.pinned, used.input, used.chunks, used.margin, used.unused)
    assert sum(parts) == 8192
    labels = [chunk.label for chunk in pack.chunks]
    assert labels == [f"C{i}" for i in range(1, len(labels) + 1)]
    rags = {chunk.rag for chunk in pack.chunks}
    assert rags == {"docs", "jira", "diffs"}
    assert pack.chunks[0].chunk_id != "docs:handbook.md#h/handbook/cafeteria"
    assert any(chunk.chunk_id.startswith("jira:PROJ-123#") for chunk in pack.chunks)
    system, user = pack.messages
    assert system.content.startswith("You help a developer")
    assert user.content.count('trust="untrusted"') == len(pack.chunks) + 1
    assert user.content.rstrip().endswith("</input>")
    assert 'label ::= "\\"C1\\""' in pack.grammar
    assert pack.debug is not None
    assert all(item.rrf_score is not None for item in pack.debug)
    real = rag.count_tokens(system.content, "ministral-3-3b") + rag.count_tokens(user.content, "ministral-3-3b")
    assert real <= 8192 - used.reserved_output - used.margin


def test_citations(setup: tuple[XatRag, Path]) -> None:
    rag, _ = setup
    pack = rag.compose(ContextRequest("log-triage", log_lines=LOGS))
    chunks = {chunk.label: chunk.chunk_id for chunk in pack.chunks}
    assert rag.check_citations(json.dumps({"answer": "x", "citations": ["C1"]}), chunks).valid
    assert not rag.check_citations(json.dumps({"answer": "x", "citations": ["C99"]}), chunks).valid


def test_text_context_and_errors(setup: tuple[XatRag, Path]) -> None:
    rag, _ = setup
    pack = rag.compose(ContextRequest("ask", question="Which port does the orders service use?"))
    assert pack.chunks[0].chunk_id == "docs:handbook.md#h/handbook/orders-service"
    with pytest.raises(NotFoundError):
        rag.compose(ContextRequest("missing", question="x"))


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
