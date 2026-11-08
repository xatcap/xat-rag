# xat-rag

The xat-rag library: recipes, the composition root, ingestion and context packs. See the [architecture](../../docs/architecture.md).

```python
from pathlib import Path
from xat_rag import ContextRequest, Settings, XatRag
from xat_rag_core.model import RawDocument, SourceKind

rag = XatRag.open(
    Settings(
        data_folder=Path("data"),
        recipes_folder=Path("recipes"),  # rags/*.toml and contexts/*.toml
        models_folder=Path("models"),
        lock_file=Path("build/models.lock.json"),
        prompts_folder=Path("prompts"),  # <name>@<version>.md, read only
    )
)
rag.ingest([RawDocument("jira:PROJ-123.json", "jira", SourceKind.JIRA, issue_json_bytes)])
rag.checkpoint("jira")
pack = rag.compose(ContextRequest("log-triage", log_lines=tuple(lines)))
# Send pack.messages, pack.grammar and pack.max_tokens to xat-llm-engine. xat-rag never calls a model.
check = rag.check_citations(answer_text, {c.label: c.chunk_id for c in pack.chunks})
```

The `XatRag` facade has these methods:

| Method | What it does |
|---|---|
| `XatRag.open(settings)` | Reads the recipes and prompts, checks and loads the models, and opens the indexes. |
| `ingest(documents)`, `ingest_one(document)` | Parse, chunk, embed and store documents. Each document has its own result. |
| `delete(rag, doc_id)` | Deletes a document, and then checkpoints the WAL file. |
| `checkpoint(rag)` | Writes the WAL file of a RAG into its file. Call it after a batch of ingestion. |
| `rebuild(rag)` | Deletes the index of a RAG and makes an empty one. The app must send the documents again. The service refuses a rebuild while a job of the RAG runs. |
| `needs_rebuild()` | Returns the RAGs whose index does not match, with the reason. |
| `search(rag, query)` | Returns the ranked candidates of one RAG, with the scores of each stage. |
| `compose(ContextRequest)` | Makes a context pack. |
| `check_citations(answer, chunks)` | Checks an answer against the labels of a pack. |
| `chunk_uris(chunk_ids)` | Returns the URI of each known chunk. |
| `count_tokens(text, generator)` | Counts tokens with the tokenizer of a generator. |
| `prompt(prompt_id)` | Returns a pinned prompt. |

The public API uses xat-rag types only. The planned `xat_rag.langchain` module is not in v0.1.

Library mode does not change the process. It does not call `harden_process`, and it turns tracing off only for its own calls (ADR 0011).
