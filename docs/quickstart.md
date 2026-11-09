# Quickstart

This page takes you from a release download to ranked chunks for a question. All commands run in a POSIX shell, on Linux or macOS. On Windows, use Git Bash and run `xat-rag-service.exe`.

## 1. Download the release

Each [release](https://github.com/xatcap/xat-rag/releases) has one service archive for each system, and one models archive.

Set the release tag and your system. The system is `linux-x64` or `macos-arm64`:

```bash
TAG=v1.0.3
SYSTEM=macos-arm64
```

Download the two archives:

```bash
BASE="https://github.com/xatcap/xat-rag/releases/download/$TAG"
curl -fLO "$BASE/xat-rag-service-$TAG-$SYSTEM.tar.gz"
curl -fLO "$BASE/xat-rag-models-$TAG.zip"
```

On Windows, the service archive is `xat-rag-service-$TAG-windows-x64.zip`.

## 2. Set up the release folder

Extract the service, and then extract the models into its `models/` folder:

```bash
tar -xzf "xat-rag-service-$TAG-$SYSTEM.tar.gz"
unzip -q "xat-rag-models-$TAG.zip" -d xat-rag-service/models
cd xat-rag-service
```

The release folder has these folders next to the executable:

| Folder | What it holds |
|---|---|
| `recipes/` | The default recipes. |
| `data/` | The indexes, and the ready file of the running service. |
| `models/` | The model files, the PDF layout models and `models.lock.json`. |

A RAG with a `pdf` source needs the PDF layout models in `models/`. Without them, the service does not start.

## 3. Start the service

> [!WARNING]
>
> The service has no authentication. Each program on the same machine can read and change the indexes, including the programs of other users. Do not put sensitive text in it on a shared machine.

Start the service. It needs no settings in a release folder:

```bash
./xat-rag-service
```

Open a second terminal in the same folder. Read the URL of the service from the ready file:

```bash
URL=$(sed -E 's/.*"url": *"([^"]+)".*/\1/' data/ready.json)
```

The port changes at each start, so read the URL again after a restart. To use a fixed port, see the [settings](api.md#settings).

Check that the service is ready:

```bash
curl -s "$URL/health/ready"
```

## 4. Structure the recipes

Recipes are TOML files. They tell xat-rag which RAGs and contexts exist.

```text
recipes/
  rags/        One file for each RAG. A RAG is one index.
    docs.toml
  contexts/    One file for each context. A context is a group of RAGs to search.
    ask-docs.toml
```

The file name must match the `name` in the recipe. The service reads the recipes when it starts, so restart it after a change.

A release has these default recipes:

| Context | RAGs | Source kind of each RAG |
|---|---|---|
| `ask-docs` | `docs` | `markdown` |
| `ask-pdfs` | `pdfs` | `pdf` |
| `ask-word` | `word` | `docx` |
| `ask-web` | `web` | `html` |
| `ask-sheets` | `sheets` | `xlsx` |
| `ask-text` | `text` | `text` |
| `ask-prompts` | `prompts` | `prompt` |
| `ask-jira` | `jira` | `jira` |
| `ask-diffs` | `diffs` | `diff` |
| `ask-all` | `all` | All nine kinds in one RAG |
| `triage` | `docs`, `jira`, `diffs` | `markdown`, `jira`, `diff` |

At start, the service creates one SQLite file in `data/` for each RAG.

### RAG recipe

```toml
# recipes/rags/docs.toml
[rag]
name = "docs"
embedder = "bge-small-en-v1.5"
reranker = "ms-marco-minilm-l6-v2"   # Optional. The default is "none".

[sources.main]                       # The table name is free. Use one source for each kind.
kind = "markdown"                    # markdown, text, diff, jira, prompt, pdf, docx, html or xlsx
max_chunk_tokens = 350               # From 32 to 2000. The chunker splits larger sections.

[retrieval]
keep = 6                             # The most chunks that this RAG gives to one request.
```

The `[retrieval]` values can change at any time. A change to `embedder`, a source `kind`, `max_chunk_tokens` or `min_page_chars` makes the index invalid. The RAG then needs a [rebuild](#rebuild-a-rag).

### Context recipe

```toml
# recipes/contexts/ask-docs.toml
[context]
name = "ask-docs"
chunks = 10                          # The default number of chunks to return.
min_score = -5                       # Optional. Chunks with a lower score are removed.

[[context.sources]]
rag = "docs"

# Add one [[context.sources]] table for each other RAG.
```

The service does not start when a recipe has one of these problems:

- A key that xat-rag does not know.
- A context that names an unknown RAG, or names a RAG twice.
- A context whose RAGs use different rerankers.
- A context with `min_score` whose RAGs have no reranker.
- A context with no reranker whose RAGs have different `rrf_k` values.

The start fails with one line that starts with `xat-rag:`. The line names the problem. The [API reference](api.md#recipes) lists all the fields.

## 5. Send requests

The examples use the `docs` RAG and the `ask-docs` context of the default recipes.

### List the RAGs and contexts

```bash
curl -s "$URL/v1/rags"
```

### Upload a document

Make a small document:

```bash
cat > orders.md <<'EOF'
# Orders service

## Ports and health

The orders service listens on port 8443.
The health check is at /health.

## Database timeouts

The connection pool waits 30 seconds for a free connection.
EOF
```

Upload the document. The service starts a job and returns its ID:

```bash
JOB=$(curl -s -X PUT "$URL/v1/rags/docs/documents" \
  -F 'documents=[{"doc_id": "docs:orders.md", "kind": "markdown", "part": "f1", "uri": "orders.md", "title": "Orders service"}];type=application/json' \
  -F 'f1=@orders.md' | sed -E 's/.*"job_id": *"([^"]+)".*/\1/')
```

### Check the job

Run this command until `status` is `done`:

```bash
curl -s "$URL/v1/jobs/$JOB"
```

Each document has its own result. A bad document gets the status `failed`, and the other documents continue.

An upload of the same bytes gives the status `unchanged`, and the file is not parsed again. This works for Markdown, text, PDF, DOCX, HTML and Excel files.

### Ask a question

```bash
curl -s -X POST "$URL/v1/context" \
  -H "Content-Type: application/json" \
  -d '{"context": "ask-docs", "question": "Which port does the orders service use?", "chunks": 5}'
```

The response has the `chunks` in rank order, best first:

```json
{"chunks": [{
   "chunk_id": "docs:orders.md#h/orders-service/ports-and-health",
   "parent_id": null,
   "kind": "section",
   "score": 8.02,
   "size_tokens": 29,
   "header": "Orders service > Ports and health",
   "text": "The orders service listens on port 8443.\nThe health check is at /health.",
   "source": {"rag": "docs", "doc_id": "docs:orders.md", "source_kind": "markdown",
              "uri": "orders.md", "title": "Orders service", "version": "",
              "span": {"type": "line", "line_start": 5, "line_end": 6}},
   "metadata": {"heading_level": 2}
 }],
 "omitted": [],
 "debug": null}
```

### See why a chunk was left out

Set `chunks` to 1 and turn on `debug`:

```bash
curl -s -X POST "$URL/v1/context" \
  -H "Content-Type: application/json" \
  -d '{"context": "ask-docs", "question": "Which port does the orders service use?", "chunks": 1, "debug": true}'
```

The `omitted` list gives the `reason` for each chunk that was found but not returned. The reason is `duplicate`, `low_score` or `limit`. The `debug` list gives the scores.

An empty `chunks` list is a success. If `omitted` has a `low_score` item, each candidate scored below `min_score`. Otherwise, the RAGs hold no matching chunks.

### Delete a document

Percent-encode the document ID:

```bash
curl -s -X DELETE "$URL/v1/rags/docs/documents/docs%3Aorders.md"
```

### Rebuild a RAG

A rebuild deletes the index of the RAG and makes an empty one.

> [!CAUTION]
>
> Keep your source documents. After a rebuild, you must upload all the documents of the RAG again.

```bash
curl -s -X POST "$URL/v1/rags/docs/rebuild"
```

## 6. What your app must do

xat-rag returns ranked text only. It does not know your LLM. Your app does this work:

1. Count the tokens of `header` and `text` with the tokenizer of your LLM. Do not use `size_tokens` for this count.
2. Add the chunks in rank order until your token budget is full.
3. Escape `text`, `header`, `title` and the `metadata` values before you put them in a prompt.
4. Tell the LLM that the chunk text is data, not instructions.
5. Give each chunk a short label, and ask the LLM to cite the labels.
6. Reject an answer that cites a label that you did not send.

> [!WARNING]
>
> A document can hold text that tries to give instructions to the LLM. Do not run commands from an answer. Do not open links from an answer without a user action.

xat-rag never retries. These errors need an action from your app:

| Error | Action |
|---|---|
| `409 index_mismatch_error` | Rebuild the RAG, and upload its documents again. |
| `503 overloaded_error` | Wait for the `Retry-After` time, and send the request again. |
| `503 service_unavailable_error` with no `Retry-After` | Restart the service. |

## 7. Run from source

The tools are [uv](https://docs.astral.sh/uv/) and Python 3.12 or later. Run these commands in a clone of the repository:

```bash
uv sync --locked
uv run python tools/fetch_models.py
```

From source, the four path settings are required:

```bash
export XatRag__DataFolder="$PWD/build/out/dev-data"
export XatRag__RecipesFolder="$PWD/eval/fixtures/setup/recipes"
export XatRag__ModelsFolder="$PWD/models"
export XatRag__LockFile="$PWD/build/models.lock.json"
export XatRag__ReadyFile="$XatRag__DataFolder/ready.json"
uv run xat-rag-service
```

## 8. Use the Python library

The library does the same work in your process, with no HTTP service. Install the `xat-rag` package from this workspace, because the repository publishes no package yet.

```python
from pathlib import Path

from xat_rag import ContextRequest, Settings, XatRag
from xat_rag_core.model import RawDocument, SourceKind

rag = XatRag.open(
    Settings(
        data_folder=Path("build/out/lib-data"),
        recipes_folder=Path("eval/fixtures/setup/recipes"),
        models_folder=Path("models"),
        lock_file=Path("build/models.lock.json"),
    )
)
try:
    data = Path("eval/fixtures/setup/documents/docs/orders.md").read_bytes()
    rag.ingest([RawDocument("docs:orders.md", "docs", SourceKind.MARKDOWN, data)])
    rag.checkpoint("docs")
    result = rag.retrieve(ContextRequest("ask-docs", "Which port does the orders service use?", chunks=5))
    for chunk in result.chunks:
        print(chunk.chunk_id, chunk.score, chunk.source.uri)
finally:
    rag.close()
```

The [API reference](api.md#python-library) lists the classes and methods.
