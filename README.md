<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/xatcap-logo-dark.svg">
    <img src="docs/assets/xatcap-logo.svg" alt="The xatcap logo: a cat with sunglasses and a witch hat" width="160">
  </picture>
</p>

# xat-rag

A RAG builder for desktop apps that use small local models. It selects the smallest set of text that answers a question, and fits it into a small context window.

> [!NOTE]
>
> xat-rag v0.1 is implemented. It has a Python library and a loopback HTTP service. Read the [architecture](docs/architecture.md), the [API reference](docs/api.md) and the [ADRs](docs/adr).

## Who it is for

xat-rag is for developers of desktop apps who want these things:

- Answers from their own docs, tickets, code changes and prompts, with a small offline model.
- A context that fits a window of 8192 tokens, with an exact token budget.
- No network access except on loopback, no telemetry and no downloads at runtime.

## How it works

1. The app sends documents to named RAGs, for example `docs`, `jira` and `diffs`. The supported formats are:
   - Markdown and plain text.
   - Unified diffs.
   - JIRA issue JSON.
   - Prompt files.
   - PDF, DOCX and HTML.
2. xat-rag splits each document by its structure and indexes it for keyword search and for vector search.
3. The app asks for a context with a question or with log lines.
4. xat-rag searches each RAG, merges and reranks the results, and packs the best chunks into the budget.
5. xat-rag returns a **context pack**: the chat messages, the chunks with labels, and a grammar for an answer with citations.
6. The app sends the messages to [xat-llm-engine](https://github.com/xatcap/xat-llm-engine). Then it asks xat-rag to check the citations in the answer.

## Why use it

- **It is made for small windows.** It uses hybrid search, reranking, quotas for each source and an exact token count. It never sends more text than the budget allows.
- **It understands technical text.** Error codes, file paths, JIRA keys and commit SHAs match exactly. Log lines become a clean query.
- **It works offline.** The embedder and the reranker are small, locked models that run on the CPU.
- **It is flexible.** TOML recipes describe each RAG and each context, so an app makes new RAG setups without code.
- **It uses LangChain interfaces.** The retrievers, the embedder and the reranker implement LangChain interfaces. In service mode, xat-rag turns LangSmith tracing off for the whole process. In library mode, it turns tracing off only for its own calls.
- **It fails in a predictable way.** Each kind of failure has one error type, and xat-rag never retries.

## What it is not

- It does not generate text, and it has no integration with xat-llm-engine. The app calls the engine itself.
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
        prompts_folder=Path("prompts"),  # <name>@<version>.md, read only
    )
)
rag.ingest([RawDocument("jira:PROJ-123.json", "jira", SourceKind.JIRA, issue_json_bytes)])
rag.checkpoint("jira")
pack = rag.compose(ContextRequest("log-triage", log_lines=tuple(lines)))
# Send pack.messages, pack.grammar and pack.max_tokens to xat-llm-engine. xat-rag never calls a model.
check = rag.check_citations(answer_text, {c.label: c.chunk_id for c in pack.chunks})
```

The library does not change the process. It turns tracing off only for its own calls.

### Service

Set the settings and start the service. The access key must have at least 16 ASCII characters.

```bash
export XatRag__DataFolder=/path/to/data
export XatRag__RecipesFolder=/path/to/recipes
export XatRag__ModelsFolder=/path/to/models
export XatRag__LockFile=/path/to/build/models.lock.json
export XatRag__AccessKey=<a random key of at least 16 characters>
export XatRag__ReadyFile=/path/to/ready.json
xat-rag-service
```

The ready file holds the URL of the service. Each request, except `GET /health/live`, sends the key:

```bash
curl -s -X POST "$URL/v1/context" -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"context": "log-triage", "log_lines": ["ERROR OrderNotFoundException: order 9f1c"]}'
```

The [API reference](docs/api.md) lists all routes, bodies, responses and settings.

## Documentation

| Page | What it covers |
|---|---|
| [Research](docs/research.md) | Industry practices for RAG with small models, the draft requirements and the open questions. |
| [Architecture](docs/architecture.md) | Packages, data model, flows, budget model, context pack, recipes, errors and open items. |
| [API reference](docs/api.md) | The HTTP routes, the request and response bodies, the errors and the settings. |
| [Development](docs/development.md) | Setup, build, test, tools, the retrieval gate, packaging and CI. |
| [Architecture decisions](docs/adr) | The significant decisions and their reasons, including the choice of LangChain core as the foundation. |
| [Writing style](docs/writing-style.md) | The writing rules for all text in this repository. |

## Development

```bash
uv sync --frozen
uv run python tools/fetch_models.py
uv run pytest
uv run pyright
uv run xat-rag-eval run --setup eval/fixtures/setup --golden eval/fixtures/golden.jsonl --data build/out/eval-data --baseline eval/baseline.json
```

The `--data` folder of the retrieval gate must be new or empty. See [Development](docs/development.md) for all commands, the tools and the CI jobs.

## License

MIT. See [LICENSE](LICENSE).
