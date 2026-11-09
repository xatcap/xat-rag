# Architecture

xat-rag stores documents in named RAGs, and it returns the most relevant chunks for a question. It is a modular monolith in one uv workspace. This page shows the layers and how they work together. The [API reference](api.md) gives the routes, fields and limits.

## System overview

```mermaid
flowchart TB
    app["Your app"]

    subgraph hosts["Host layer"]
        service["xat-rag-service<br/>loopback HTTP, jobs, lifecycle"]
        eval["xat-rag-eval<br/>golden sets, retrieval gate"]
    end

    subgraph root["Composition layer"]
        lib["xat-rag<br/>XatRag, recipes, ingestion, retrieve"]
    end

    subgraph adapters["Adapter layer"]
        parsers["xat-rag-parsers<br/>text kinds, chunker"]
        documents["xat-rag-documents<br/>PDF, DOCX, HTML, Excel with Docling"]
        onnx["xat-rag-onnx<br/>embedder, reranker"]
        tokenizers["xat-rag-tokenizers<br/>size ruler"]
        sqlite["xat-rag-sqlite<br/>store, FTS5, sqlite-vec"]
    end

    core["xat-rag-core<br/>domain model, ports, Markdown form, query, RRF, MMR, merge"]
    offline["xat-rag-offline<br/>environment and network guard"]
    files[("data/ one SQLite file for each RAG<br/>models/ locked ONNX files<br/>recipes/ TOML files")]

    app -- "HTTP on loopback" --> service
    app -- "Python calls" --> lib
    service --> lib
    service --> offline
    eval --> lib
    eval --> core
    lib --> parsers & documents & onnx & tokenizers & sqlite
    lib --> core
    parsers & documents & onnx & tokenizers & sqlite --> core
    lib -. "reads and writes through adapters" .-> files
```

An arrow means "depends on". The `uv run lint-imports` command checks these dependencies in CI.

## Layers

| Layer | Packages | Job | Import rule |
|---|---|---|---|
| Host | `xat-rag-service`, `xat-rag-eval` | Give xat-rag to a caller. The service adds HTTP, request checks, upload parsing, jobs and the lifecycle. The evaluation package measures the retrieval quality. | Can import the composition layer. The composition layer cannot import the service. |
| Composition | `xat-rag` | Load the recipes, connect the adapters and run the two use cases: ingestion and retrieval. It holds the public class `XatRag`. | Can import the adapters and the domain. |
| Adapter | `xat-rag-parsers`, `xat-rag-documents`, `xat-rag-onnx`, `xat-rag-tokenizers`, `xat-rag-sqlite` | Connect one external technology to a port of the domain. | Can import the domain only. Adapters do not import each other. |
| Domain | `xat-rag-core` | Hold the model, the ports, the errors, sanitizing, the Markdown form, the spans, the query build, RRF, MMR and the merge. | Imports no other workspace package. |
| Hardening | `xat-rag-offline` | Remove secret variables, and block network access outside loopback. | Imports no workspace package. |

## Ports and adapters

The domain and the composition layer use these ports. The ports are `Protocol` classes. `Embedder`, `Retriever`, `Reranker` and `TokenCounter` are in `xat_rag_core/ports.py`. `SectionParser` is in `xat_rag_core/parsing.py`.

| Component | Port | Adapter |
|---|---|---|
| Parser | `SectionParser` | One parser for each source kind, in `xat-rag-parsers` and `xat-rag-documents` |
| Chunker | None | `StructuralChunker` |
| Embedder | `Embedder` | `OnnxEmbeddings` |
| Keyword search | `Retriever` | `Fts5Retriever` |
| Vector search | `Retriever` | `VecRetriever` |
| Reranker | `Reranker` | `OnnxReranker` |
| Store and manifest | None | `SqliteRagStore` and `IndexManifest` |
| Size ruler | `TokenCounter` | `HfTokenCounter` |

PDF, DOCX, HTML and Excel parsers use Docling. Each one returns sections. Docling is not visible outside `xat-rag-documents`.

## Start of the service

The order matters, because the hardening must run before any other library import.

1. `main` reads the data folder with the standard library only.
2. `harden_process` cleans the environment and installs the network guard.
3. `run` reads the settings and takes the lock of the data folder.
4. `run` deletes a stale ready file.
5. `XatRag.open` loads the recipes, checks the model files and opens the indexes.
6. The server starts to listen on loopback.
7. The service writes the ready file with its URL.

A failure in these steps stops the service with exit code 2.

## Ingestion

The caller pushes documents. xat-rag never fetches a document by itself.

```mermaid
sequenceDiagram
    actor Caller
    participant Service as xat-rag-service
    participant Job as Job worker of the RAG
    participant Parser as Parser and chunker
    participant Embedder as OnnxEmbeddings
    participant Store as SqliteRagStore

    Caller->>Service: PUT /v1/rags/{rag}/documents
    Service-->>Caller: 202 job_id
    Service->>Job: Queue the documents
    loop For each document
        Job->>Parser: Bytes and source kind
        Parser-->>Job: Sanitized chunks with spans
        Job->>Store: Compare content hashes
        Store-->>Job: New, changed and removed chunks
        Job->>Embedder: New and changed chunks only
        Embedder-->>Job: Vectors
        Job->>Store: Write the document in one transaction
    end
    Job->>Store: Checkpoint the WAL file
    Caller->>Service: GET /v1/jobs/{job_id}
    Service-->>Caller: Status and one result for each document
```

- Each RAG has one worker thread. Jobs of one RAG run in order, and jobs of different RAGs run in parallel.
- SQLite runs in WAL mode, so questions continue during ingestion.
- An upload that matches the stored upload returns `unchanged`, and it is not parsed. This applies to Markdown, text, PDF, DOCX, HTML and Excel.
- A bad document gets a `failed` result. The other documents continue.
- Jobs live in memory, so a restart loses them. The indexes stay correct, because each document is one transaction.

## Retrieval

```mermaid
sequenceDiagram
    actor Caller
    participant Service as xat-rag-service
    participant Retrieve as Retrieve use case
    participant Search as Fts5Retriever and VecRetriever
    participant Reranker as Cross-encoder
    participant Merge as Merge in xat-rag-core

    Caller->>Service: POST /v1/context
    Service->>Retrieve: ContextRequest
    Retrieve->>Retrieve: Sanitize the question and build the query
    loop For each RAG in the context
        Retrieve->>Search: Keyword terms and dense text
        Search-->>Retrieve: Two ranked lists
        Retrieve->>Retrieve: Fuse the lists with RRF
        Retrieve->>Reranker: Top candidates
        Reranker-->>Retrieve: Rerank scores
        Retrieve->>Retrieve: Remove duplicates, MMR, cut to keep
    end
    Retrieve->>Merge: Candidates of all RAGs
    Merge-->>Retrieve: Sorted, filtered by min_score, cut to chunks
    Retrieve-->>Service: RetrievalResult
    Service-->>Caller: chunks, omitted, debug
```

- Keyword search finds exact terms, such as error codes, file paths and JIRA keys. Vector search finds text that uses other words.
- All RAGs of a context use the same reranker, so their scores can be compared.
- Blocking work runs in a pool of two worker threads, and never on the event loop.
- Each stage has a time limit in the context recipe. A stage that passes its limit gives `stage_timeout_error`.
- Ties in a score are broken by `chunk_id`, so one input gives one order on one platform.

## Index safety

Each RAG is one SQLite file. The file has a manifest that records the embedder, the chunkers, the tokenizer versions and a hash of the recipe fields that change the index. A chunker version includes the parser version. For Markdown, text, PDF, DOCX, HTML and Excel, it also includes the split rules.

xat-rag compares the manifest with the loaded models and recipes. On a mismatch, it refuses the index and returns `index_mismatch_error`. It never embeds again without a request. The caller rebuilds the RAG and uploads the documents again.

The model files are locked by `build/models.lock.json`. xat-rag checks their size and sha256 at start, and it never downloads a file at runtime. The PDF layout models are checked too, when a RAG has a PDF source.

## Boundaries

| xat-rag does | The app does |
|---|---|
| Parse, chunk and index the documents that the app sends. | Find the documents and send them. |
| Build a search query from a question. | Prepare a long or noisy question. |
| Return ranked chunks with their sources. | Count tokens and choose the chunks that fit its LLM. |
| Sanitize control characters. | Escape the text, and build the prompt. |
| Give a stable ID for each chunk. | Call the LLM and check the citations. |

xat-rag accepts only loopback connections, and it has no authentication. Each program on the same machine can use the service. xat-rag sends no telemetry. ONNX Runtime telemetry is off, and on Windows ONNX Runtime may still send one event at its first start. The text of a chunk is untrusted, so the app must not treat it as instructions.
