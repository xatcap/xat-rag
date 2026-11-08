# Architecture

This page describes xat-rag v0.1, as the code implements it. The [ADRs](adr) record the decisions and their reasons. The [API reference](api.md) lists the HTTP routes. The [development guide](development.md) lists the build and test commands. The [app guide](app-guide.md) describes the work that the app does with the results.

## 1. Goal and boundaries

xat-rag retrieves structured information from its database and returns it. It works with any LLM, offline or frontier. It does not know which LLM the app uses. See [ADR 0014](adr/0014-retrieval-only-and-app-side-fitting.md).

xat-rag does these tasks:

- It ingests documents into named RAGs.
- It turns a question into a search query.
- It searches each RAG, merges the results and returns the best chunks in rank order.

The app does these tasks. xat-rag does not do them:

- It does not count tokens for an LLM.
- It does not fit chunks into a context window or a token budget.
- It does not build prompts, system messages or chat messages.
- It does not add tags, labels or escaping to the text of a chunk.
- It does not check the citations in an answer.
- It does not call an LLM.

Changing the LLM of the app needs no rebuild of the index. Recipes do not name an LLM, so they need no change either.

| In scope (v1) | Out of scope (v1) |
|---|---|
| English content | Multilingual models (opt-in later) |
| Markdown, plain text, unified diffs, JIRA issue JSON, prompt files, PDF, DOCX, HTML | Code files chunked by syntax tree (tree-sitter) |
| PDF pages with a text layer | OCR of scanned pages, table structure, encrypted PDFs |
| BM25, dense search, RRF, cross-encoder reranking | Sentence pruning (v1.1, if the evaluation shows a need) |
| Push-only ingestion | Network connectors to JIRA or GitHub |
| A loopback HTTP service and a Python library | Encryption at rest |
| LangChain core interfaces as the ports | `langchain-community`, `langchain-classic`, LCEL pipelines |

## 2. Package layout

xat-rag is a modular monolith in one uv workspace. It follows the [Python guidelines](python/index.md). LangChain is the foundation of the interfaces. See [ADR 0001](adr/0001-langchain-core-as-foundation.md).

```
packages/
  xat-rag-offline/     xat_rag_offline     Startup hardening: environment, tracing, network guard. No dependencies.
  xat-rag-core/        xat_rag_core        Domain model, ports, lc_codec, errors, sanitizing, query,
                                           RRF, MMR, the merge of the RAG results, ids and parsing types.
  xat-rag-parsers/     xat_rag_parsers     Parsers for markdown, text, diff, jira and prompt. Structural chunker.
  xat-rag-documents/   xat_rag_documents   Parsers for pdf (pypdf), docx (python-docx) and html
                                           (beautifulsoup4), with parse limits.
  xat-rag-onnx/        xat_rag_onnx        OnnxEmbeddings, OnnxCrossEncoderCompressor, model lock and file checks.
  xat-rag-tokenizers/  xat_rag_tokenizers  HfTokenCounter, the fixed size ruler.
  xat-rag-sqlite/      xat_rag_sqlite      SqliteRagStore (writes, manifest, deletes), Fts5Retriever
                                           and VecRetriever.
  xat-rag/             xat_rag             Public library facade (XatRag), recipes, ingestion
                                           and the retrieve use case.
  xat-rag-service/     xat_rag_service     Loopback HTTP host, ingestion jobs, uploads and the lifecycle.
  xat-rag-eval/        xat_rag_eval        Golden sets, metrics and the retrieval gate.
tools/                                     fetch_models.py and check_dependencies.py.
                                           Build and check scripts. They are not shipped.
build/                                     release.py, the PyInstaller spec and runtime hook,
                                           models.lock.json, models.sources.json, dependencies.allow.json.
```

Dependency rules:

- `xat-rag-offline` imports no workspace package and no LangChain package. The facade and the service import it before any other module.
- `xat-rag-core` depends on no other package of the workspace.
- Each adapter package depends only on `xat-rag-core`.
- `xat-rag` connects the adapters. It is the composition root.
- `xat-rag-service` and `xat-rag-eval` depend on `xat-rag`.
- No package imports `langchain_community`, `langchain_classic`, `langchain` or `tiktoken`.
- `uv run lint-imports` checks these rules in CI.

The public library API uses xat-rag types. LangChain types appear inside the packages, in the retrievers, the embedder and the reranker. A separate `xat_rag.langchain` module that exposes these objects is planned. It is not in v0.1.

The library declares version ranges (for example `langchain-core>=1.7.6,<2`), so that it can be installed next to a LangChain app. The service uses the exact versions of `uv.lock`, and it pins FastAPI and uvicorn exactly.

## 3. Ports

LangChain classes are the ports where LangChain has a matching interface. The other parts are xat-rag classes or small protocols. Tests use scripted fakes.

| Component | LangChain interface | Our implementation |
|---|---|---|
| Parser (bytes to sections) | None | One `SectionParser` for each kind: markdown, text, diff, jira, prompt, pdf, docx, html. `SectionBlobParser` wraps a parser as a LangChain `BaseBlobParser`. |
| Chunker | None | `StructuralChunker`. It uses `RecursiveCharacterTextSplitter` for sections that are too large. |
| Embedder | `Embeddings` | `OnnxEmbeddings` |
| Lexical search | `BaseRetriever` | `Fts5Retriever` (SQLite FTS5) |
| Vector search | `BaseRetriever` | `VecRetriever` (sqlite-vec) |
| Reranker | `BaseDocumentCompressor` | `OnnxCrossEncoderCompressor` |
| Store and manifest | None | `SqliteRagStore` and `IndexManifest` |
| Size ruler | None | `HfTokenCounter` (the `TokenCounter` protocol is in `xat_rag_core.ports`) |
| Query | None | `build_query()` in `xat_rag_core.query` |
| Fusion, MMR, merge of the RAG results | None | Custom |

Rules for the use of LangChain:

- **`Document` only at port boundaries.** Inside xat-rag, `Chunk` and `Candidate` are frozen dataclasses. `xat_rag_core.lc_codec` converts between them and `Document`. All reads and writes of `Document.metadata` go through `lc_codec`.
- **`Document.id` is the `chunk_id`.** `page_content` is the chunk text without its header.
- **No LCEL pipeline.** The use cases are plain Python. They call `.invoke()` on each retriever, so each stage has its own time limit, error type, debug ranks and `chunk_id` tie-break.
- **No `with_retry` and no `with_fallbacks`.** They break [ADR 0009](adr/0009-error-model.md).
- **Sync calls only, inside our worker pool.** The default `ainvoke` and `aembed_*` use the default asyncio executor, which is outside the pool and the queue limit.
- **Tracing is off for each call.** Each use case runs `no_tracing()` in its worker thread. It clears the LangChain tracer context variables and enters `tracing_context(enabled=False)`. It passes no callbacks. See [ADR 0011](adr/0011-offline-hardening.md).

Each use case calls each retriever with its own query text:

- `Fts5Retriever` gets the lexical terms, joined with spaces.
- `VecRetriever` gets the dense text.

v1.1 adds a `Pruner` port.

`RetrievalQuery` has separate parts, because each search stage needs different text:

| Field | Used by | Meaning |
|---|---|---|
| `lexical_terms` | `Fts5Retriever` | At most 32 terms. Each term is unique without case, and code names stay whole. |
| `dense_text` | `VecRetriever` | The whole question, after sanitizing. The embedder keeps only the part that fits its input. |
| `rerank_text` | The reranker | The first 600 characters of the question. The reranker cuts the query to 256 tokens. |
| `filters` | `Fts5Retriever` and `VecRetriever` | Hard metadata filters. The store applies them before the top-k cut. `build_query()` never sets them. |

Filters are not reachable through recipes or HTTP. No recipe key and no route sets a filter, and the context request has no filter field. The method `XatRag.search` passes the filters of a `RetrievalQuery` to the retrievers, so a Python caller that builds the query by hand can set them. This path is internal in v0.1. Apps must not rely on it.

## 4. Data model

### Document

A document has these fields:

- `doc_id`: the ID that the caller sends.
- `rag` and `source_kind`.
- `uri` and `title`.
- `version`: for example a commit SHA or the JIRA `updated` value.
- `metadata`: a map of scalar values, sent by the caller. The values of strings are sanitized.
- `content_hash`: the sha256 of the raw bytes.

### Chunk

A chunk has these fields:

- `chunk_id` and `doc_id`.
- `parent_id`: the ID of the parent section, when there is one. A JIRA comment has the `desc` chunk of its issue as its parent. If the parent is split, `parent_id` is the ID of its first piece. Other chunks have no parent in v0.1.
- `kind`: `section`, `hunk`, `description`, `comment`, `prompt` or `page`.
- `header` and `text`.
- `span`.
- `metadata`. It holds the metadata of the document, and then the fields that the parser writes. A parser field wins when both have the same name.
- `content_hash`: the sha256 of the header and the text.
- `chunker_version` and `token_count`. The store keeps the count. The API shows it as `size_tokens`.

The `span` has one of three forms:

| Span | Source kinds | Fields |
|---|---|---|
| `LineSpan` | Markdown, text, diff, JIRA, prompt, HTML | `line_start`, `line_end`. For HTML, these are lines of the HTML source (`Tag.sourceline`), so that a viewer can open the source. |
| `PageSpan` | PDF | `page_start`, `page_end` (absolute page numbers, plus the page labels for display) |
| `BlockSpan` | DOCX | `block_start`, `block_end`: the index of the first and last body block (a paragraph or a table), because a DOCX file has no stable pages |

#### Size ruler

`size_tokens` is a measure from the fixed size ruler of xat-rag. The ruler is the bundled tokenizer of Ministral-3-3B. It is a locked file, and its lock role is `size_tokenizer`. The ruler has no link to the LLM of the app. A count with the ruler can differ a lot from the count of the app's LLM.

The ruler counts the header and the text of a chunk together. It is used for these values:

- `size_tokens` in each chunk result.
- The limit `max_chunk_tokens` of each source in the RAG recipe. The default is 350, and the range is 32 to 2000. The chunker splits a section that is larger than this limit.

The counter does not treat special tokens in a text as special. A text such as `[INST]` counts as text. The splitter uses the same count.

The header and the text must also fit the **embedder** input. The chunker allows at most 510 tokens, with the special tokens, out of the 512 that `bge-small-en-v1.5` accepts. This limit uses the tokenizer of the embedder, and not the size ruler.

When a section is too large, the chunker splits it again. It splits at paragraph, line and word boundaries first. A single token that is still too large is cut at a character position. The embedder never cuts a document text. It raises an error instead.

The reranker gets the query and the chunk. The query is cut to 256 tokens. If the pair is still longer than 512 tokens, the reranker cuts the chunk text at the end. The `debug` block then shows `rerank_truncated: true`.

### Chunk IDs

A chunk ID is `<doc_id>#<anchor>`. The anchor comes from the structure of the document. These rules apply:

- A heading slug keeps the letters and digits of any script. Accents are removed, so `Über` gives `uber`. Other characters become `-`.
- A slug never has `~`, `/` or `#`.
- If two sections get the same anchor, the later one gets `~2`, then `~3`, and so on.
- A section that is split gets pieces with the ID `<section id>/~s<N>`. A piece ID never equals a heading anchor, because a slug cannot hold `~`.
- A parent that is split points to its first piece as `parent_id`.
- An empty section gets no chunk. It keeps its ID slot, so that the IDs of later sections do not change when it gets text.

| Source | Anchor | Example chunk ID |
|---|---|---|
| Markdown | Heading path, `h/<slug>/<slug>`. Text before the first heading uses `h`. | `docs:orders.md#h/orders-service/ports-and-health` |
| Text | `t` | `docs:notes.txt#t` |
| Diff | `f/<path>/hunk/<N>`, where N counts the hunks of the file | `diffs:4f2a9c1.diff#f/src/OrderService.java/hunk/1` |
| JIRA | `desc` for the summary and description. `comment/<id>` for each comment. | `jira:PROJ-123.json#desc`, `jira:PROJ-123.json#comment/10023` |
| Prompt file | `prompt` | `prompts:style-guide.md#prompt` |
| PDF with an outline | Heading path | `docs:manual.pdf#h/install/linux` |
| PDF without an outline | `p/<N>`, the absolute page number | `docs:manual.pdf#p/12` |
| DOCX | Heading path. Tables stay in their section. | `docs:spec.docx#h/requirements/security` |
| HTML | Heading path | `docs:guide.html#h/setup/proxy` |

When a document comes again, xat-rag compares the `content_hash` of each chunk. It embeds only the chunks that are new or changed. It deletes the chunks that are gone.

### Index manifest

Each RAG has a manifest. xat-rag stores it in the `meta` table of the SQLite file, when the file is created. `GET /v1/rags/{rag}` returns part of it.

- `rag`, `index_format` (1 in v0.1) and `xat_rag_version`.
- `index_hash`: a hash of the recipe fields that change the index only. These are the embedder, and for each source the kind, `max_chunk_tokens` and `min_page_chars`. The `[retrieval]` fields are not in this hash, so a change to `keep` or `rrf_k` needs no rebuild.
- `embedder`: `model_id`, `sha256`, `dims`, `normalize`, `query_prefix` and `document_prefix`. The `sha256` is the sha256 of the text `<model.onnx sha>:<tokenizer.json sha>`.
- `lexical_tokenizer`: `code-v1`.
- `chunkers`: for each source kind, `<parser version>+lcts<langchain-text-splitters version>`. Parser versions include the library versions. For example, a PDF parser version includes the pypdf version.
- `size_tokenizer_id`: `ministral-3-3b-tokenizer@<first 12 hex digits of the locked tokenizer.json sha256>`.

An index written before ADR 0014 has the key `generator_tokenizer_id`. xat-rag reads that key as `size_tokenizer_id`. The value is the same, so such an index needs no rebuild.

If the loaded models, chunkers or recipe do not match the manifest, xat-rag refuses to use the index. It returns `index_mismatch_error` (409) for each route that uses that RAG. The `xat_rag_version` can differ, because it does not change the index. The app starts a rebuild. xat-rag never embeds again in silence. See [ADR 0005](adr/0005-sqlite-store-and-index-manifest.md).

### Code-aware lexical tokens

`Fts5Retriever` prepares the text before it inserts it into FTS5. It keeps the full compound and it adds its parts:

- `com.shop.OrderNotFoundException` gives `com_shop_ordernotfoundexception`, `com`, `shop`, `ordernotfoundexception`, `order`, `not`, `found` and `exception`.
- `snake_case` gives `snake_case`, `snake` and `case`. `camelCase` gives `camelcase`, `camel` and `case`.
- A SHA such as `4f2a9c1` stays one token.
- A JIRA key such as `PROJ-123` gives `proj_123`, `proj` and `123`. A question that has the key finds the issue through these tokens.

The version of these rules is in the manifest, because a change needs a rebuild.

The Python `sqlite3` module cannot register a custom FTS5 tokenizer. Thus, xat-rag expands the text before insertion. FTS5 uses `unicode61` on the result, with the underscore as a token character. The same expansion runs on the query terms.

The question is untrusted text. Characters such as `-` and `:` are operators in the FTS5 query syntax. Thus, xat-rag puts each token in double quotes, with inner quotes doubled. It joins the tokens with `OR`. It never passes raw text to `MATCH`.

`Filters` bind each value as an SQL parameter. The vector search adds a filter as a `rowid IN` condition inside the nearest-neighbor query, before the top-k cut. xat-rag loads only the locked sqlite-vec file. It calls `enable_load_extension(False)` after that load.

### Why not the LangChain community retrievers

`BM25Retriever`, `SQLiteVec` and `EnsembleRetriever` were measured and rejected. See [ADR 0001](adr/0001-langchain-core-as-foundation.md). In short:

- `BM25Retriever` keeps the whole index in Python memory. It has no persistence and no filter before the top-k cut.
- `SQLiteVec` has no keyword search, no filters and no delete.
- `EnsembleRetriever` sends the same text to each retriever and returns no ranks for each stage.

### Document mapping for LangChain

`lc_codec` converts a `Chunk` to a LangChain `Document` and back. The `ChunkMeta` typed dictionary lists all metadata keys that xat-rag writes. The keys include `chunk_id`, `doc_id`, `rag`, `source_kind`, `uri`, `title`, `span`, `parent_id`, the source metadata and the scores of each stage. Code outside `lc_codec` never reads `Document.metadata` directly.

## 5. Ingestion flow

```
caller --PUT /v1/rags/{rag}/documents (multipart)--> service --> 202 {job_id}
                                                                 |
  Job (worker thread of the RAG) -----------------------------+
  bytes --> parser (SectionParser for the kind) --> sections with spans and sanitized text
        --> structural chunker: headers, and split pieces for large sections --> chunks
        --> compare content_hash with the store --(unchanged)--> skip
        --> size ruler count (stored with the chunk)
        --> OnnxEmbeddings (batches of 32, new or changed chunks only)
        --> SqliteRagStore: one transaction for each document

caller --GET /v1/jobs/{job_id}--> status, the count of done documents,
                                  and one result for each document
```

- Parsing comes first, because PDF, DOCX and HTML are binary or markup. Sanitizing runs on the extracted text, so the spans and the chunk `content_hash` come from the same text that xat-rag stores.
- An ingestion job runs in the background. A large batch takes minutes on a CPU. `DELETE /v1/jobs/{job_id}` cancels the job before its next document.
- Each RAG has one worker thread. Jobs of one RAG run in order, and jobs of other RAGs run in parallel. SQLite runs in WAL mode, so queries continue during ingestion.
- Jobs are kept in memory. A restart of the service loses them. The service keeps the last 200 finished jobs.
- The service checkpoints the WAL file when a job finishes all its documents. A cancelled job does not checkpoint. A library caller calls `XatRag.checkpoint` after a batch.
- A document result has one of these statuses: `created`, `updated`, `unchanged` or `failed`. A `failed` result has an `error_type`, a `reason` and a `message`.
- A document that raises a xat-rag error gets its own `failed` result. The other documents continue. An unexpected error ends the job with the status `failed`.

Limits:

- 256 documents and 64 MiB for each upload request. A request with more bytes gets 413 `invalid_request_error`. A request with more documents gets 400 `invalid_request_error`.
- 4 MiB for each text document (Markdown, text, diff, JIRA and prompt).
- 8 MiB for each HTML document.
- 32 MiB for each PDF or DOCX document.
- Upload bytes that wait in the queue of all jobs can total 256 MiB. More uploads get 503 `overloaded_error` with `Retry-After: 5`.

A document that is too large gets `parse_error` with the reason `too_large`.

A job that stops does not repeat finished work when the caller sends the same documents again. Unchanged chunks are skipped.

### PDF, DOCX and HTML

See [ADR 0012](adr/0012-document-parsers.md). The parsers run in the process that calls xat-rag. A parser has no network access. The caller sends the bytes, and no parser opens a path or a URL.

| Kind | Library (license) | Rules |
|---|---|---|
| PDF | `pypdf` 7.0.0 (BSD-3-Clause) | Text from each page. The outline gives the heading path when it exists. Encrypted files are refused. Tables become plain text. Invisible text (white text or render mode 3) is not detected in v1. This is a known risk of prompt injection. |
| DOCX | `python-docx` 1.2.0 (MIT), with `lxml` (BSD-3-Clause) | `Heading 1` to `Heading 9` styles give the heading path. Tables become pipe rows in their section. Tracked insertions are kept, and the result has a note. Deleted text and field codes are dropped. Hidden text is removed. Files with macros are refused: xat-rag checks the content type and a `vbaProject.bin` part, not the file name. |
| HTML | `beautifulsoup4` 4.15.0 (MIT), with the standard `html.parser` | `h1` to `h6` give the heading path. Active and metadata elements (for example `script`, `style`, `iframe`, `object` and `svg`) are removed. Hidden elements are removed. They have the `hidden` attribute, `aria-hidden="true"`, or a style with `display: none`, `visibility: hidden`, `opacity: 0` or `font-size: 0`. `pre` keeps its line breaks. Nothing is fetched. |

The DOCX parser adds these notes to the result when they apply: `comments_skipped`, `headers_footers_skipped` and `tracked_insertions_kept`. A `gridSpan` is never expanded into cells. A grid span above 64 is a limit error.

**Scanned PDFs.** v1 has no OCR. A page has a text layer when its sanitized text has at least `min_page_chars` (default 20) non-space characters. xat-rag indexes only those pages. The document result has `pages_total`, `pages_indexed` and `skipped_pages`. Each skipped page has a `page`, a `label` and a `reason`. The reasons are `no_text`, `extract_error` and `page_text_limit`. If no page has text, the document fails with `parse_error` and the reason `no_text_layer`. Page numbers in chunk IDs are absolute. Later OCR adds chunks, and it does not rename the existing ones.

**Limits against bad files.** A Python thread cannot be stopped from outside. Thus, the limits act before and during the parse:

- One binary parse runs at a time, for all RAGs of the process.
- Each document has a deadline of 60 seconds. The parsers check it between pages, blocks, sections and comments. The clock starts after the parse lock is taken. The service does not read a setting for the deadline in v1.
- A check that finds the deadline passed gives `parse_error` with the reason `parse_timeout`.
- PDF: at most 2000 pages, 200k characters for each page and 20M characters for each document. The decompression limits of pypdf are lower. An error on one page skips only that page.
- DOCX: before the parse, at most 10k zip entries, 256 MiB uncompressed in total, 64 MiB for `word/document.xml` and a compression ratio of 100 for each large entry.
- HTML: at most 8 MiB of raw input. At most 500k `<` characters, which is an upper bound for the elements. A recursion error gives a limit error.

A limit that a file passes gives `parse_error` with the reason `limit_exceeded`. The message names the limit.

A parse can also stop without reaching a check. If a document runs longer than twice the deadline plus 300 seconds (420 seconds by default), a watchdog marks the service **degraded**:

- `GET /health/ready` returns 503 with `{"status": "degraded", "reason": "parse_timeout"}`.
- `PUT /v1/rags/{rag}/documents` returns 503 `service_unavailable_error` with no `Retry-After`. The message tells the app to restart the service.
- Context requests continue, but they can be slow.

Index writes are atomic, so the index stays correct.

The limits reduce the risk, but they do not remove it. There is no memory limit, so a file can make the whole process stop. pypdf is pure Python and holds the GIL. A long page can delay the context requests, and their stage time limits can then fire late. One page or one large XML part cannot be stopped in the middle. These are known risks of parsing in the process.

## 6. Query and retrieval flow

### Query

`build_query()` in `xat_rag_core.query` makes the query for a question. It does these steps:

1. It removes control, zero-width and bidirectional characters, and it normalizes the text to NFC. Line breaks and tabs become spaces.
2. It finds the terms of the text. Each term is kept once, without case. Code names stay whole, for example `OrderService.java` or `PROJ-123`. The punctuation at the end of a term is removed. At most 32 terms are kept.
3. It keeps the whole text as `dense_text`. The embedder then keeps only the part that fits its input, which is 512 model tokens with the query prefix. The end of a very long question is not used for vector search.
4. It keeps the first 600 characters as `rerank_text`. The reranker uses only these characters.

A context request with no text after step 1 gives 400 `invalid_request_error`. A question longer than 20000 characters also gives 400. The same question always gives the same query.

### Retrieval

```
caller --POST /v1/context {context, question, chunks}-->

 1  build_query --> RetrievalQuery
 2  For each RAG in the context recipe:
       Fts5Retriever (lexical_k, default 40) ----+
                                                 +--> RRF (rrf_k, default 60)
       VecRetriever (dense_k, default 40) -------+    --> top rerank_candidates (40)
 3  Cross-encoder reranks the candidates with rerank_text (if the RAG has a reranker)
 4  Remove duplicates (same content hash, or cosine similarity of 0.95 or more)
    --> MMR (lambda 0.7) picks at most `keep` chunks for each RAG
 5  Merge all RAGs: sort by score, then by chunk_id. Remove repeated chunk IDs.
    Cut the list to `chunks`. The `pack` time limit applies to this step.
 6  Build the result: `chunks` in rank order, and `omitted` with the reasons

caller <-- RetrievalResult
```

- All RAGs in one context must use the same reranker, so that their rerank scores can be compared. If the context has no reranker, all its RAGs must use the same `rrf_k`. xat-rag refuses a context recipe that breaks these rules.
- The reranker keeps all candidates of its RAG in score order. MMR then chooses the chunks of that RAG.
- The same query is embedded once for each RAG. A context with three RAGs embeds it three times.
- Blocking work (ONNX Runtime and SQLite) runs in a pool of two worker threads, never on the event loop.
- A request that takes too long fails with `stage_timeout_error` (504). xat-rag checks each stage limit after the stage ends. A stage is not stopped in the middle. This is a known limit.
- At most `XatRag__QueueLimit` requests can wait or run in the pool. More requests get `overloaded_error` (503, `Retry-After: 1`).
- Ties in a score are broken by `chunk_id`. Thus, the same input gives the same result on one platform. ONNX scores can differ a little between CPUs, so the determinism test runs on each platform separately.

Each RAG has its own candidates, so the merge sorts them in one list. All RAGs of one context use the same reranker, so their rerank scores are on one scale.

## 7. Result of a context request

`POST /v1/context` returns a `RetrievalResult`. Its JSON form has three fields: `chunks`, `omitted` and `debug`. The [API reference](api.md#context) has the full shape of each field.

- `chunks` holds the chunks in rank order, best first. Each chunk has its `text`, `header`, `source`, `metadata` and `size_tokens`.
- `omitted` lists the candidates that were found but not returned. The reason is `duplicate` or `limit`.
- `debug` holds the scores of each candidate. It is `null` unless the request sets `debug`.

The rules of the result are these:

- `chunks` is an upper bound. Each RAG gives at most its `keep` chunks, and the merge gives at most the number of `chunks` in the request. A result can have fewer chunks than requested. For example, one RAG with the default `keep` of 8 gives at most 8 chunks for a request for 10.
- The `score` of a chunk is the rerank score when the RAG has a reranker. Otherwise it is the RRF score.
- xat-rag returns no text that is not in a chunk. It adds no tags, labels or escaping.
- `text` and `header` are the stored text. They can hold any content that the document had. They are untrusted (section 14).
- A chunk whose document is deleted during the request is left out. It is not listed in `omitted`.

## 8. The work of the app

xat-rag stops at the ranked chunks. The app does these tasks. The [app guide](app-guide.md) gives the details.

- It counts the tokens of each chunk with the tokenizer of its LLM.
- It selects the chunks that fit its prompt.
- It builds the prompt, with its own system message and instructions.
- It escapes the text, the header, the title and the metadata before it puts them in a prompt.
- It checks the citations of the answer. It uses the `chunk_id` and the `source` values that it received.

xat-rag keeps no record of past requests. The app keeps the chunks that it needs for its citations.

## 9. HTTP service

See [ADR 0003](adr/0003-library-and-loopback-service.md). The service accepts only loopback connections. All routes are under `/v1`, except the health routes. The [API reference](api.md) lists the routes, the bodies, the responses, the errors, the settings and the start order.

RAG names and context names match `[a-z0-9][a-z0-9-]{0,63}`.

### Access control

Loopback alone is not enough. Other programs and other users on the machine can connect to `127.0.0.1`, and a web page can try to reach it through the browser. The [API reference](api.md#access-and-the-request-checks) lists the checks. Each check has this purpose:

- **The access key** stops other programs on the machine. The app makes a new random key at each start.
- **The `Host` check** stops DNS rebinding.
- **The `Origin` check** and the absence of CORS headers stop web pages.
- **A Unix socket** on macOS and Linux lets the file permissions decide who can connect. The socket is `0600`.

### Lifecycle

The [API reference](api.md#lifecycle) lists the start order. A stale ready file from an earlier run is deleted at start.

A startup failure stops the service. A bad recipe, a bad model or a bad setting gives a message and exit code 2 or another non-zero code.

## 10. Recipes

Recipes are TOML files. xat-rag reads them with `tomllib` and validates them with pydantic. An unknown key is an error, and the message names the field. The `format` field gives the version of the file format, and it is 1.

A **RAG recipe** describes one index:

```toml
# rags/jira.toml
[rag]
name = "jira"
format = 1
embedder = "bge-small-en-v1.5"
reranker = "ms-marco-minilm-l6-v2"   # Optional. The default is "none".

[sources.main]
kind = "jira"                        # markdown, text, diff, jira, prompt, pdf, docx or html
max_chunk_tokens = 350               # Size ruler tokens, 32 to 2000. The default is 350.
min_page_chars = 20                  # PDF only. The default is 20.

[retrieval]                          # These values do not change the index.
lexical_k = 40
dense_k = 40
rrf_k = 60
rerank_candidates = 40
keep = 8                             # The most chunks that this RAG gives to one request.
mmr_lambda = 0.7
```

The name of a source table, such as `[sources.main]`, is free. A RAG can have one source for each kind.

A **context recipe** names a group of RAGs:

```toml
# contexts/ask-docs.toml
[context]
name = "ask-docs"
format = 1
chunks = 10                          # The default number of chunks, 1 to 100. The default is 10.

[[context.sources]]
rag = "docs"

[[context.sources]]
rag = "jira"

[limits]                             # Milliseconds for each stage of a context request.
query_build = 200
retrieve = 500
embed = 1000
rerank = 2500
pack = 300                           # The merge of the RAG results.
```

In a context recipe, `name` is required. Each `[[context.sources]]` table has only the field `rag`. The other fields have these defaults:

- `format` is 1.
- `chunks` is 10. A request can set another number from 1 to 100.
- The `[retrieval]` and `[limits]` values in the examples are the defaults.

xat-rag refuses these recipes at startup, with a message that names the problem:

- A context that names a RAG twice.
- A context that names an unknown RAG.
- A context whose RAGs use different rerankers.
- A context whose RAGs use no reranker and have different `rrf_k` values.
- A RAG with two sources of the same kind.
- A key that the recipe does not know. The message names the field, for example `context.generator`. Recipes of the version before ADR 0014 have keys that are now unknown, and they must be edited.
- A file name that does not match the recipe name.
- A file that is not UTF-8 or not valid TOML.

Apps make new RAG setups by writing recipes, not code. Service settings use environment variables with the prefix `XatRag__`. Ingestion jobs have no stage limits, because their time depends on the batch size. The caller can cancel a job.

## 11. Errors

See [ADR 0009](adr/0009-error-model.md). Each kind of failure has one error type and one status code. xat-rag never retries. There is no silent fallback. The [API reference](api.md#errors) lists the error types and their status codes.

Error messages never repeat input values. xat-rag replaces the default validation messages of pydantic and FastAPI. Each validation message names the fields that failed, and never the input value.

A failure at startup stops the process with a non-zero exit code and a message that tells what to do. Examples are a model with a wrong sha256, sqlite-vec that does not load, and a bad recipe.

## 12. Models

See [ADR 0004](adr/0004-onnx-runtime-and-locked-models.md).

- ONNX Runtime runs the embedder and the reranker in the service process, on the CPU.
- `OnnxEmbeddings` implements LangChain `Embeddings`. It adds the query prefix to each query and no prefix to each document. It also has `embed_documents_np` and `embed_query_np`, which return `NDArray[np.float32]`. xat-rag calls these. The list methods exist only for LangChain callers.
- A query longer than the model input is cut to fit. A query is user input, so it is cut and not refused. A document longer than 512 WordPiece tokens raises `internal_error`. The chunker must split such a document first.
- `OnnxCrossEncoderCompressor` implements LangChain `BaseDocumentCompressor`. It cuts the query to 256 tokens. It sets `rerank_truncated` when it cuts a passage. A failure raises `StageError`. It never returns the input unchanged.
- LangChain's `FastEmbedEmbeddings`, `HuggingFaceEmbeddings` and `CrossEncoderReranker` are not used, because they download models or need torch.
- The v1 models are:
  - `bge-small-en-v1.5` (MIT), the embedder. It has 384 dimensions. It is an ONNX int8 conversion of the upstream model.
  - `ms-marco-MiniLM-L6-v2` (Apache-2.0), the reranker. It is an ONNX int8 conversion of the upstream model.
- The size ruler is the tokenizer of Ministral-3-3B (Apache-2.0). Only its tokenizer files are locked, under the role `size_tokenizer`. The model weights are not used.
- `build/models.lock.json` lists each model with its repository, revision and license. It lists each file of each model with its size and sha256.
- `tools/fetch_models.py` gets the files at build time only. xat-rag never downloads at runtime.
- At startup, xat-rag checks the size and sha256 of the files that the runtime uses: `model.onnx` and `tokenizer.json` of each ONNX model, and `tokenizer.json` of the size ruler. The check result is cached in `model-check-cache.json` in the data folder. The cache key is the path, the size, the modification time, the inode and the sha256.
- A program that can write to the data folder can also change the cache. The app must protect the data folder (section 14).
- xat-rag loads tokenizers only from local files (`Tokenizer.from_file`). It never calls `from_pretrained`, which can download.

## 13. Evaluation

`xat-rag-eval` measures retrieval quality on golden sets, and its gate blocks a change when the quality drops past a fixed limit. It calls no LLM, so it uses no judge model. The [development guide](development.md#retrieval-gate) gives the rules of the gate and the commands. The [package README](../packages/xat-rag-eval/README.md) gives the golden-set format and the report.

The gate measures the final list of a context request. For each case, it checks the recall of the first 3, 5 and 10 chunks, and the MRR of the final list. It also checks the invariants of the final list. The invariants are these. The list has at most the limit of chunks. Its chunk IDs are unique. Its scores do not rise. The same case gives the same list on a second run.

The gate fails when these values drop:

- The recall at 5 and at 10 of the final list, by more than 2 points.
- The recall at 10 of the reranked stage of the case RAG, by more than 2 points.
- The final MRR, by more than 10 points.

The source coverage is reported only. It is the share of cases in which the case RAG gives at least one chunk to the final list.

The fixture set in `eval/fixtures` has no PDF, DOCX or HTML golden cases yet. Ablation runs (no lexical search, no dense search, no reranker) are manual in v1.

The targets for v1 apply to a machine with a 4-core x64 or Apple Silicon CPU, 8 GB of RAM and no GPU. The app loads its LLM on that machine. The fixture set is too small to measure the targets:

- `POST /v1/context` at 100k chunks: p95 of at most 300 ms without the reranker, and at most 1.5 s with the reranker for a context with 3 RAGs.
- Memory: at most 500 MB of extra RAM for the whole service at 100k chunks.
- Ingestion: at least 1000 chunks per minute.

## 14. Privacy and untrusted content

See [ADR 0010](adr/0010-untrusted-content-privacy-and-ingestion.md), [ADR 0011](adr/0011-offline-hardening.md) and [ADR 0014](adr/0014-retrieval-only-and-app-side-fitting.md).

### Offline hardening

`langchain-core` ships the `langsmith` client, and tracing can send text to a remote server. [ADR 0011](adr/0011-offline-hardening.md) gives the full rules. In short:

- **Service mode.** Before any LangChain import, the service removes the tracing variables and the variables that can hold secrets. An audit hook then refuses process starts, unknown native libraries and network events outside loopback. The hook is defense in depth only, because native code can pass it.
- **Both modes.** Each use case turns tracing off inside its worker thread. In library mode, this is the only protection, because xat-rag does not change the process of the host app.
- **CI.** The `offline` job runs the service and library tests in a network namespace with only loopback.

The service depends on `opentelemetry-api`, because FastAPI requires it. xat-rag does not configure it. It has no exporter and no SDK, so it sends nothing. The package is on the dependency allowlist.

### Logs and telemetry

- The service code logs only the class name of an unexpected exception. It logs no document IDs, chunk IDs, text or queries.
- Logs of the third-party libraries (pypdf, python-docx, beautifulsoup4, uvicorn access, httpx, langsmith and onnxruntime) are off.
- xat-rag sends no telemetry and opens no network connection except on loopback.

### Storage and deletion

- The indexes are in the data folder that the app owns. They hold copies of sensitive text. The app must protect the folder, for example with the disk encryption of the operating system.
- The data folder is created with mode `0700`.
- v1 has no encryption at rest. This is a known limit.
- A delete removes the document from all searches when the call returns. The facade and the service checkpoint the WAL file after each delete.
- To remove the deleted bytes from the files, xat-rag does these steps:
  - It sets `PRAGMA secure_delete = ON` on each connection that it opens.
  - It turns on the FTS5 `secure-delete` option of the lexical index.
  - It runs `PRAGMA wal_checkpoint(TRUNCATE)` after a delete. If readers block the checkpoint, it tries again for about 5 seconds, and then it raises an error.
- The tests check that deleted lexical text is gone from the files. They do not check the shadow tables of sqlite-vec yet. Vectors can leak text, so this check is an open item (section 16).
- Software cannot remove all copies. SSD wear leveling, file system journals, backups and snapshots can keep old data. To remove a whole RAG, delete its file. The docs of the app must tell this to its users.

### Personal data

- The JIRA parser does not write author or assignee names into the text or the metadata.
- Free text can still hold names, for example commit messages, `Signed-off-by` lines and mentions in comments. xat-rag does not find these. The app must remove them before it sends the documents, if its policy requires it.

### Untrusted content

- Sanitizing runs on the text that the parser extracts, before chunking. It removes ANSI codes and control characters, except tab, line feed and carriage return. It removes zero-width characters, bidirectional controls (U+202A to U+202E and U+2066 to U+2069), Unicode tag characters (U+E0000 to U+E007F) and variation selectors. It turns NEL and the line and paragraph separators into line feeds. Then it normalizes the text to NFC. The same sanitizing runs on the question.
- The text of a chunk, its header and its title are untrusted. The metadata values are untrusted too. xat-rag does not wrap, escape or label them. The app must do this before it puts them in a prompt. See the [app guide](app-guide.md).
- A chunk can hold text that tries to give instructions to an LLM (prompt injection). The app must not run commands or open links from an answer without a user action.
- xat-rag cannot find a prompt injection in a document. The app must treat all returned text as data.

## 15. Extension points

v1 has no plugin loading. A change to one of these parts needs a change to the code:

- **A new source kind.** Add the kind to the `SourceKind` enum, to the recipe `SourceKindName`, and to `make_parser` in `xat_rag/index.py`. Write a parser that returns sections, and a chunker rule if the kind needs one. The parser must not read files or the network. A new kind may need a new library. Such a library must pass the dependency allowlist.
- **A new embedder or reranker.** Add the model to the lock, and add a spec to `EMBEDDERS` or `RERANKERS` in `xat_rag/models.py`. Each model needs an ONNX export and a check.
- **A new store.** The store is `SqliteRagStore`. The retrievers use it directly. A second store needs a new retriever set, and it is a larger change.
- **LangChain users.** A `xat_rag.langchain` module is planned. v0.1 does not have it. The retrievers, the embedder and the reranker are LangChain objects in the packages, and an app can import them.

## 16. Later versions and open items

Later versions:

- Chunk code files by syntax tree (tree-sitter).
- OCR for scanned PDF pages, table structure in PDF, and encrypted PDFs.
- The `xat_rag.langchain` module, with a LangChain `VectorStore` adapter.
- Plugin loading for source kinds.
- Neighbor expansion of chunks.
- Sentence pruning for prose sources.
- Multilingual embedder and reranker as recipe options.
- Ablation runs in CI.
- Deterministic links between sources, such as a stack frame to a diff hunk or a JIRA key to a commit.

Open items in v0.1:

- The DOCX parser does not set the `lxml` options `resolve_entities=False` and `huge_tree=False`. The zip checks and the grid span check run before the parse.
- A test does not check that sqlite-vec overwrites deleted vectors. Section 14 gives the reason.
- The CI `offline` job uses a network namespace. It does not use a packet capture.
- The service has no setting for the parse deadline.
- The build does not write a separate OpenAPI document, so the rule of a published document in [ADR 0003](adr/0003-library-and-loopback-service.md) is not met yet.
- The docs do not yet tell app developers how to add an outbound firewall rule, as [ADR 0011](adr/0011-offline-hardening.md) requires.
- One `eval/baseline.json` is shared by the three operating systems. A per-system baseline can be needed, because int8 ONNX numerics can differ a little by CPU and batch. See the [development guide](development.md#retrieval-gate).
- The frozen folder is checked for denied modules by `check-bundle`. The check runs only in the package job of CI.
- The fixture golden set has a small number of identifier and near-match cases, and its corpus is small. A comparison on a larger golden set is necessary to measure how the removal of exact lookups changed the ranking of identifiers. See [ADR 0013](adr/0013-remove-log-handling-and-exact-lookups.md).
