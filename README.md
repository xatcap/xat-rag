<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/xatcap-logo-dark.svg">
    <img src="docs/assets/xatcap-logo.svg" alt="The xatcap logo: a cat with sunglasses and a witch hat" width="160">
  </picture>
</p>

# xat-rag

A RAG builder for desktop apps. It finds the most relevant text for a question and returns it ranked, as structured data, for any LLM.

## Who it is for

xat-rag is for developers of desktop apps who want these things:

- Retrieved text from their own docs, tickets, code changes and prompt files.
- Results that the app can check, cite and show, with a link to each source.
- No network access except on loopback, and no downloads at runtime.

## How it works

1. The app sends documents to named RAGs. The formats are:

   - Markdown and text.
   - Unified diffs.
   - JIRA issue JSON and prompt files.
   - PDF, DOCX, HTML and Excel (`.xlsx`).
2. xat-rag splits each document by its structure, and indexes it for keyword search and vector search.
3. The app asks a question in a named context. A context is a group of RAGs.
4. xat-rag searches each RAG, reranks the results and merges them.
5. xat-rag returns the best chunks in rank order, each with its text and source.

Read [how it works](docs/how-it-works.md) for each step.

## What it is not

- It does not call an LLM, write answers, build prompts or fit text to a token limit. The app does these tasks.
- It does not connect to JIRA, GitHub or other network services. The app sends the documents.
- It is not a server for a team. It accepts requests only from programs on the same machine, and it has no authentication.

## Telemetry

xat-rag sends no telemetry. It turns off the telemetry of ONNX Runtime, the library that runs its models, by a variable and by an API call. This was checked on macOS only. On Windows, ONNX Runtime may still send one event when it first starts.

## Quick start

Download the service and models archives from the [releases](https://github.com/xatcap/xat-rag/releases), and extract the models into the `models/` folder of the service. Then start the service, and ask a question at the URL in `data/ready.json`:

```bash
./xat-rag-service
```

```bash
curl -s -X POST "$URL/v1/context" \
  -H "Content-Type: application/json" \
  -d '{"context": "ask-docs", "question": "Which port does the orders service use?", "chunks": 5}'
```

The [quickstart](docs/quickstart.md) gives all the commands, including the upload of documents.

## Documentation

| Page | What it covers |
|---|---|
| [Quickstart](docs/quickstart.md) | Set up a release, structure recipes and send requests. |
| [How it works](docs/how-it-works.md) | Each step of ingestion and retrieval, with diagrams. |
| [Architecture](docs/architecture.md) | The layers and how the components work together. |
| [API reference](docs/api.md) | The HTTP routes, recipes, settings, limits and the Python library. |
| [Changelog](CHANGELOG.md) | The changes between versions. |

## Development

```bash
uv sync --locked
uv run python tools/fetch_models.py
uv run pytest
uv run pyright
```

## License

MIT. See [LICENSE](LICENSE).
