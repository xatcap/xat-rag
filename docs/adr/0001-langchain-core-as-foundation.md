# 0001. Use LangChain core interfaces as the foundation, and keep the budget, query and store stages our own

- Status: Accepted
- Date: 2026-11-07
- Deciders: Project maintainer

## History

The first version of this ADR (2026-11-07) chose no framework. The maintainer then chose LangChain as the foundation. The maintainer also chose our own retrievers on LangChain interfaces, after measurements of the LangChain community retrievers. The facts below were checked against the source and the releases on 2026-11-07.

## Context

xat-rag must fit retrieved text into an 8192-token window with an exact budget. It must work offline, download nothing at runtime and send no telemetry. It must have a stable SemVer API, and it must ship inside signed desktop installers. It must read Markdown, text, diffs, JIRA JSON, prompts, PDF, DOCX and HTML in v1.

A framework gives a shared vocabulary, interfaces that other developers know, and ready-made splitters. No framework has the main stages of xat-rag: an exact token budget with quotas for each source, a stable order, a log query builder and a citation check.

## Decision

LangChain core is the foundation of the interfaces. xat-rag ships two LangChain packages, with exact version pins:

| Package | Version | License | Use |
|---|---|---|---|
| `langchain-core` | 1.7.6 | MIT | `Document`, `Blob`, `BaseBlobParser` (only in an adapter), `Embeddings`, `BaseRetriever`, `BaseDocumentCompressor`. |
| `langchain-text-splitters` | 1.1.3 | MIT | `RecursiveCharacterTextSplitter`, only to split sections that are too large. Its `length_function` is our `TokenCounter`. |

Our own code implements these interfaces. See [architecture, section 3](../architecture.md#3-ports):

- `OnnxEmbeddings` (`Embeddings`) and `OnnxCrossEncoderCompressor` (`BaseDocumentCompressor`), on ONNX Runtime with locked files.
- `Fts5Retriever`, `VecRetriever` and `ExactLookupRetriever` (`BaseRetriever`), over one SQLite file for each RAG.
- One parser for each source kind, and one structural chunker. The parsers implement a xat-rag `SectionParser` protocol. `SectionBlobParser` adapts a parser to LangChain `BaseBlobParser`.

These stages stay our own, because LangChain has no equivalent: the document store and manifest, the token counter, the query builders, RRF fusion, MMR, the budget packer, the renderer and the citation validator.

Rules:

- xat-rag does not ship `langchain`, `langchain-community`, `langchain-classic` or `tiktoken`. An import-linter rule and a dependency allowlist enforce this.
- The pipeline is plain Python, not LCEL. xat-rag never uses `with_retry` or `with_fallbacks`.
- The public library API and the HTTP API use xat-rag types. A planned `xat_rag.langchain` module will expose the LangChain objects for LangChain users, and it will follow the LangChain major version.
- The service and the frozen app use the exact versions above, from `uv.lock`. The library declares ranges (`langchain-core>=1.7.6,<2`, `langchain-text-splitters>=1.1.3,<2`), so that it can be installed next to a LangChain app.
- Tracing is off for each xat-rag call, and in service mode for the whole process. See [ADR 0011](0011-offline-hardening.md).
- Upgrades come in a monthly batch, and security fixes at once. Each upgrade must pass the evaluation gate, the determinism test, the offline tests, the allowlist and the chunk-boundary test. A new LangChain major version needs a new ADR.

## Options that we considered

### LangChain ready-made retrievers

`BM25Retriever` and `SQLiteVec` (`langchain-community` 0.4.2), and `EnsembleRetriever` (`langchain-classic` 1.0.8). We read their source and measured them. We rejected them:

- `langchain-community` is being retired. Its import shows: "`langchain-community` is being sunset and is no longer actively maintained". It is 0.x, outside the 1.x stability promise, and it brings SQLAlchemy, aiohttp and requests. `rank_bm25` has had no release since 2022.
- `BM25Retriever` keeps the whole index in Python memory. A measurement with 20k chunks gave about 264 MB and 50 ms for each 30-term query. Extrapolated to 100k chunks: about 1.8 GB, 3.5 GB during a rebuild, and 250 ms for each query. It has no persistence (only an unsafe pickle), no filter before the top-k cut, and no delete without a full rebuild. It also returns documents with a score of zero to fill `k`.
- `SQLiteVec` has no keyword search, no filters and no delete. It stores the text and the vectors twice, and its `add_texts` commits the open transaction.
- `EnsembleRetriever` sends the same text to every retriever. It removes duplicates by text unless `id_key` is set, and it returns no ranks for each stage.

### Other frameworks

| Option | Version | License | Reason for rejection |
|---|---|---|---|
| LangChain, as the full foundation with `langchain` and LCEL | 1.4.9 | MIT | The `langchain` package is for agents and brings LangGraph. LCEL hides the stage time limits, typed errors and debug ranks that xat-rag needs. |
| LangGraph | 1.2.20 | MIT | It runs agent state machines. xat-rag has a linear, deterministic pipeline. |
| LlamaIndex (`llama-index-core`) | 0.14.25 | MIT | The default LLM and embedder are OpenAI. Without the `openai` package, an `ImportError` occurs. Without a key, a `ValueError` occurs. **With a key on the user's machine, it calls OpenAI.** It ships NLTK and tiktoken data in the wheel, but it downloads if `NLTK_DATA` points to an empty folder. It has the largest dependency tree, and 0.x minor releases break the API. |
| Haystack (`haystack-ai`) | 3.4.1 | Apache-2.0 | Telemetry is **on by default** (PostHog, to `eu.posthog.com`). The decision is made at import, and `HAYSTACK_TELEMETRY_ENABLED=False` must be set first. It writes a user ID to `~/.haystack/config.yaml`. `openai` and `posthog` are required dependencies. Version 3.0 (2026-07-20) removed many APIs. Its converters are the best option if LangChain is changed later. |
| Chroma | 1.5.9 | Apache-2.0 | `anonymized_telemetry` is still `True` by default, but the product telemetry client now does nothing. Its default embedder downloads a MiniLM model from S3. It brings grpcio, kubernetes and OpenTelemetry. |
| txtai | 9.14.0 | Apache-2.0 | Needs torch, transformers and faiss. Downloads models from the Hugging Face hub. |
| FastEmbed | 0.9.1 | Apache-2.0 | Downloads models from the Hugging Face hub or GCS into a temporary folder, unless a local path is set. It has no sha256 lock. |
| sentence-transformers, transformers | 6.2.0, 5.22.2 | Apache-2.0 | Need torch. Use the hub by default. Allowed only in `tools/`. |
| DSPy | | MIT | It optimizes prompts, which needs a generator. xat-rag calls no generator. |
| RAGatouille, ColBERT | | MIT, Apache-2.0 | Need torch. Large late-interaction indexes. |
| Semantic Kernel, Microsoft.Extensions.AI, Kernel Memory | | MIT | .NET only (see [ADR 0002](0002-python.md)). |
| R2R, RAGFlow | | MIT, Apache-2.0 | Server platforms. They do not fit inside a desktop app. |
| LightRAG, GraphRAG | | MIT | Indexing asks an LLM to extract entities, and xat-rag calls no generator. |
| No framework | | | The first version of this ADR. Changed by the maintainer, for the shared interfaces and the splitters. |

## Consequences

- LangChain users can use the xat-rag retrievers, embedder and reranker directly.
- The stages that make xat-rag work in a small window stay our code. The evaluation gate stays necessary.
- The `langsmith` client ships inside `langchain-core`, with requests, httpx, orjson, zstandard and websockets. It adds about 15 to 25 MB and some start time. [ADR 0011](0011-offline-hardening.md) controls the risk, but it cannot remove the client.
- `Document.metadata` is an untyped dictionary. All reads and writes go through `lc_codec` functions. Pyright "unknown type" exceptions are allowed only there and in the retriever base classes, with the reason written down.
- LangChain releases patches several times a week. Exact pins mean that we stay behind on purpose, and each upgrade costs a CI run.
- A change in `RecursiveCharacterTextSplitter` can move chunk boundaries. The manifest records its version, so the change gives `index_mismatch_error` and a planned rebuild.
