<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/xatcap-logo-dark.svg">
    <img src="docs/assets/xatcap-logo.svg" alt="The xatcap logo: a cat with sunglasses and a witch hat" width="160">
  </picture>
</p>

# xat-rag

A RAG builder for desktop apps. It finds the most relevant text for a question and returns it ranked, as structured data, for any LLM.

> [!NOTE]
>
> xat-rag v0.1 is implemented. It has a Python library and a loopback HTTP service. Read the [API reference](docs/api.md), the [architecture](docs/architecture.md) and the [app guide](docs/app-guide.md).

## Who it is for

xat-rag is for developers of desktop apps who want these things:

- Retrieved text from their own docs, tickets, code changes and prompt files, for any LLM, offline or frontier.
- Results that the app can check, cite and show, with a link to each source.
- No network access except on loopback, no telemetry and no downloads at runtime.

## How it works

1. The app sends documents to named RAGs, for example `docs`, `jira` and `diffs`. The supported formats are:
   - Markdown and plain text.
   - Unified diffs.
   - JIRA issue JSON.
   - Prompt files.
   - PDF, DOCX and HTML.
2. xat-rag splits each document by its structure and indexes it for keyword search and for vector search.
3. The app asks a question in a named context. A context is a group of RAGs.
4. xat-rag searches each RAG, merges the results and reranks them.
5. xat-rag returns the best chunks in rank order. Each chunk has its text, its header, its source and its metadata.
6. The app counts the tokens, fits the chunks to its LLM, builds the prompt, escapes the text and checks the citations.

## Why use it

- **It returns structured results.** Each chunk has a stable ID, its text and a source with a link to the origin. The app decides how to use them.
- **It finds exact terms.** Error codes, file paths, JIRA keys and commit SHAs are found by keyword search with code-aware tokens. Vector search finds text that uses other words.
- **It works offline.** The embedder and the reranker are small, locked models that run on the CPU.
- **It is flexible.** TOML recipes describe each RAG and each context, so an app makes new setups without code.
- **It does not depend on an LLM.** The app can change its LLM. No index rebuild or recipe change is needed.
- **It uses LangChain interfaces.** The retrievers, the embedder and the reranker implement LangChain interfaces. In service mode, xat-rag turns LangSmith tracing off for the whole process. In library mode, it turns tracing off only for its own calls.
- **It fails in a predictable way.** Each kind of failure has one error type, and xat-rag never retries.

## What it is not

- It does not call an LLM. It does not write answers, build prompts or fit text to a token limit. The app does these tasks.
- It does not connect to JIRA, GitHub or other network services. The app sends the documents.
- It does not offer an embeddings API for other programs.
- It is not a server for a team. It accepts requests only from programs on the same machine.

## Quick start

### Library

Install the `xat-rag` package from this workspace. The repository has no publish step yet.

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
    )
)
rag.ingest([RawDocument("jira:PROJ-123.json", "jira", SourceKind.JIRA, issue_json_bytes)])
rag.checkpoint("jira")
result = rag.retrieve(ContextRequest("ask-jira", "Why does the refund fail?", chunks=5))
for chunk in result.chunks:
    print(chunk.chunk_id, chunk.source.uri, chunk.text)
```

The library does not change the process. It turns tracing off only for its own calls.

Before the text goes to an LLM, count its tokens with the tokenizer of that LLM. See the [app guide](docs/app-guide.md).

### Service

A release folder starts the service with no setting. It has these folders next to the executable:

- `recipes/` holds the default recipes.
- `data/` holds the indexes. It is empty in a release.
- `models/` holds the model files. It is empty in a release.

Extract the models archive, `xat-rag-models-<tag>.zip`, into `models/`. Then start the service. On Windows, run `xat-rag-service.exe`:

```bash
./xat-rag-service
```

From source, set the settings and start the service:

```bash
export XatRag__DataFolder=/path/to/data
export XatRag__RecipesFolder=/path/to/recipes
export XatRag__ModelsFolder=/path/to/models
export XatRag__LockFile=/path/to/build/models.lock.json
export XatRag__ReadyFile=/path/to/ready.json
xat-rag-service
```

The service has no authentication. Each program on the same machine, including the programs of other users, can use it. Read [ADR 0015](docs/adr/0015-remove-service-access-key.md) before you use the service on a shared machine.

The ready file holds the URL of the service. In a release, it is `data/ready.json`. Use that URL in each request:

```bash
curl -s -X POST "$URL/v1/context" \
  -H "Content-Type: application/json" \
  -d '{"context": "ask-docs", "question": "Which port does the orders service use?", "chunks": 5}'
```

The response has `chunks` in rank order, the `omitted` chunks and `debug`. The [API reference](docs/api.md) lists all routes, bodies, responses and settings.

## Documentation

| Page | What it covers |
|---|---|
| [Architecture](docs/architecture.md) | Packages, data model, ingestion and retrieval flows, the result, recipes, errors and open items. |
| [API reference](docs/api.md) | The HTTP routes, the request and response bodies, the errors and the settings. |
| [App guide](docs/app-guide.md) | The work of the app: tokens, prompts, escaping, citations and the question. |
| [Development](docs/development.md) | Setup, build, test, tools, the retrieval gate, packaging and CI. |
| [Changelog](CHANGELOG.md) | The changes between versions, with the upgrade steps. |

## Development

```bash
uv sync --locked
uv run python tools/fetch_models.py
uv run pytest
uv run pyright
uv run xat-rag-eval run --setup eval/fixtures/setup --golden eval/fixtures/golden.jsonl --data build/out/eval-data --baseline eval/baseline.json
```

The `--data` folder of the retrieval gate must be new or empty. See [Development](docs/development.md) for all commands, the tools and the CI jobs.

## License

MIT. See [LICENSE](LICENSE).
