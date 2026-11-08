# Architecture

This page describes xat-rag v0.1, as the code implements it. The [research](research.md) gives the reasons for the techniques. The [ADRs](adr) record the decisions. The [API reference](api.md) lists the HTTP routes, and the [development guide](development.md) lists the build and test commands.

## 1. Goal and boundaries

xat-rag selects the smallest set of text that answers a question. It fits that text into the token budget of a small local model. It does these tasks:

- It ingests documents into named RAGs.
- It builds a query from a question or from log lines.
- It retrieves, reranks and packs chunks into a **context pack** for one request.
- It validates the citations in the model's answer.

xat-rag **never calls a generator**. The app sends the context pack to its generator itself. See [ADR 0008](adr/0008-context-pack-and-citations.md).

| In scope (v1) | Out of scope (v1) |
|---|---|
| English content | Multilingual models (opt-in later) |
| The local generator (Ministral-3-3B) | Token profiles for cloud generators |
| Markdown, plain text, unified diffs, JIRA issue JSON, prompt files, PDF, DOCX, HTML | Code files chunked by syntax tree (tree-sitter) |
| PDF pages with a text layer | OCR of scanned pages, table structure, encrypted PDFs |
| BM25, dense search, RRF, cross-encoder reranking | Sentence pruning (v1.1, if the evaluation shows a need) |
| Masking and template grouping of log lines | The full Drain algorithm |
| Push-only ingestion | Network connectors to JIRA or GitHub |
| A loopback HTTP service and a Python library | Encryption at rest |
| LangChain core interfaces as the ports | `langchain-community`, `langchain-classic`, LCEL pipelines |

## 2. Package layout

xat-rag is a modular monolith in one uv workspace. It follows the [Python guidelines](python/index.md). LangChain is the foundation of the interfaces. See [ADR 0001](adr/0001-langchain-core-as-foundation.md).

```
packages/
  xat-rag-offline/     xat_rag_offline     Startup hardening: environment, tracing, network guard. No dependencies.
  xat-rag-core/        xat_rag_core        Domain model, ports, lc_codec, errors, sanitizing, RRF, MMR,
                                           budget, packer, renderer, citation check, query types.
  xat-rag-parsers/     xat_rag_parsers     Parsers for markdown, text, diff, jira and prompt. Structural chunker.
  xat-rag-documents/   xat_rag_documents   Parsers for pdf (pypdf), docx (python-docx) and html
                                           (beautifulsoup4), with parse limits.
  xat-rag-logs/        xat_rag_logs        LogQueryBuilder: masking, templates, identifiers, input section.
  xat-rag-onnx/        xat_rag_onnx        OnnxEmbeddings, OnnxCrossEncoderCompressor, model lock and file checks.
  xat-rag-tokenizers/  xat_rag_tokenizers  HfTokenCounter and the generator profiles.
  xat-rag-sqlite/      xat_rag_sqlite      SqliteRagStore (writes, manifest, deletes) and the Fts5Retriever,
                                           VecRetriever and ExactLookupRetriever.
  xat-rag/             xat_rag             Public library facade (XatRag), recipes, prompts, ingestion
                                           and the compose use case.
  xat-rag-service/     xat_rag_service     Loopback HTTP host, ingestion jobs, uploads and the lifecycle.
  xat-rag-eval/        xat_rag_eval        Golden sets, metrics and the retrieval gate.
tools/                                     fetch_models.py, measure_profile.py, check_dependencies.py.
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
| Exact lookups | `BaseRetriever` | `ExactLookupRetriever` (SQL on metadata fields) |
| Reranker | `BaseDocumentCompressor` | `OnnxCrossEncoderCompressor` |
| Store and manifest | None | `SqliteRagStore` and `IndexManifest` |
| Token counter | None | `HfTokenCounter` (the `TokenCounter` protocol is in `xat_rag_core.ports`) |
| Query builder | None | `TextQueryBuilder` (`text-v1`) and `LogQueryBuilder` (`log-v1`) |
| Fusion, MMR, packer, renderer, citation check | None | Custom |

Rules for the use of LangChain:

- **`Document` only at port boundaries.** Inside xat-rag, `Chunk`, `Candidate` and `ContextPack` are frozen dataclasses. `xat_rag_core.lc_codec` converts between them and `Document`. All reads and writes of `Document.metadata` go through `lc_codec`.
- **`Document.id` is the `chunk_id`.** `page_content` is the chunk text without its header.
- **No LCEL pipeline.** The use cases are plain Python. They call `.invoke()` on each retriever, so each stage has its own time limit, error type, debug ranks and `chunk_id` tie-break.
- **No `with_retry` and no `with_fallbacks`.** They break [ADR 0009](adr/0009-error-model.md).
- **Sync calls only, inside our worker pool.** The default `ainvoke` and `aembed_*` use the default asyncio executor, which is outside the pool and the queue limit.
- **Tracing is off for each call.** Each use case runs `no_tracing()` in its worker thread. It clears the LangChain tracer context variables and enters `tracing_context(enabled=False)`. It passes no callbacks. See [ADR 0011](adr/0011-offline-hardening.md).

Each use case calls each retriever with its own query text:

- `Fts5Retriever` gets the lexical terms, joined with spaces.
- `VecRetriever` gets the dense text.
- `ExactLookupRetriever` gets the lookups, and it uses no text.

v1.1 adds a `Pruner` port.

`RetrievalQuery` has separate parts, because each search stage needs different text:

| Field | Used by | Example from log lines |
|---|---|---|
| `lexical_terms` | `Fts5Retriever`. At most 32 terms. | `NullPointerException`, `OrderService.java`, `PROJ-123` |
| `dense_text` | `VecRetriever`. At most 2000 characters. | The question and the masked templates, joined |
| `rerank_text` | The reranker. At most 600 characters, and the reranker cuts it to 256 tokens. | The question and the top three templates |
| `exact_lookups` | `ExactLookupRetriever`, a third ranked list for RRF. At most 16 lookups. | `jira_key = "PROJ-123"`, `path_name = "orderservice.java"` (the lowercase file name) |
| `filters` | Hard metadata filters, from the recipe or the request only | `project = "shop"` |

Exact lookups add candidates. They never remove candidates. So a stack frame in one file does not hide a relevant diff in another file. Only `filters` remove candidates. They apply inside each retriever, before the top-k cut.

## 4. Data model

### Document

A document has these fields:

- `doc_id`: the ID that the caller sends.
- `rag` and `source_kind`.
- `uri` and `title`.
- `version`: for example a commit SHA or the JIRA `updated` value.
- `metadata`: a map of scalar values.
- `content_hash`: the sha256 of the raw bytes.

### Chunk

A chunk has these fields:

- `chunk_id` and `doc_id`.
- `parent_id`, when the chunk came from a split section.
- `kind`: `section`, `hunk`, `description`, `comment`, `prompt` or `page`.
- `header` and `text`.
- `span`.
- `metadata`.
- `content_hash`: the sha256 of the header and the text.
- `chunker_version` and `token_count`. The count uses the generator tokenizer, and the store keeps it.

The `span` has one of three forms:

| Span | Source kinds | Fields |
|---|---|---|
| `LineSpan` | Markdown, text, diff, JIRA, prompt, HTML | `line_start`, `line_end`. For HTML, these are lines of the HTML source (`Tag.sourceline`), so that a viewer can open the source. |
| `PageSpan` | PDF | `page_start`, `page_end` (absolute page numbers, plus the page labels for display) |
| `BlockSpan` | DOCX | `block_start`, `block_end`: the index of the first and last body block (a paragraph or a table), because a DOCX file has no stable pages |

A chunk has two size limits:

- `max_chunk_tokens` in the source recipe counts **generator** tokens. The default is 350, and the range is 32 to 2000. The packer uses this count.
- The header and the text must also fit the **embedder** input. The chunker allows at most 510 tokens, with the special tokens, out of the 512 that `bge-small-en-v1.5` accepts.

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
| Prompt, retrieved from a RAG | `prompt` | `prompts:log-triage-system.md#prompt` |
| PDF with an outline | Heading path | `docs:manual.pdf#h/install/linux` |
| PDF without an outline | `p/<N>`, the absolute page number | `docs:manual.pdf#p/12` |
| DOCX | Heading path. Tables stay in their section. | `docs:spec.docx#h/requirements/security` |
| HTML | Heading path | `docs:guide.html#h/setup/proxy` |

Pinned prompts are not chunks. They have IDs such as `prompt:log-triage-system@3`, and the context pack lists them in `pinned`.

When a document comes again, xat-rag compares the `content_hash` of each chunk. It embeds only the chunks that are new or changed. It deletes the chunks that are gone.

### Index manifest

Each RAG has a manifest. xat-rag stores it in the `meta` table of the SQLite file, when the file is created. `GET /v1/rags/{rag}` returns part of it.

- `rag`, `index_format` (1 in v0.1) and `xat_rag_version`.
- `index_hash`: a hash of the recipe fields that change the index only. These are the embedder, and for each source the kind, `max_chunk_tokens` and `min_page_chars`. The `[retrieval]` fields are not in this hash, so a change to `keep` or `rrf_k` needs no rebuild.
- `embedder`: `model_id`, `sha256`, `dims`, `normalize`, `query_prefix` and `document_prefix`. The `sha256` is the sha256 of the text `<model.onnx sha>:<tokenizer.json sha>`.
- `lexical_tokenizer`: `code-v1`.
- `chunkers`: for each source kind, `<parser version>+lcts<langchain-text-splitters version>`. Parser versions include the library versions. For example, a PDF parser version includes the pypdf version.
- `generator_tokenizer_id`: `ministral-3-3b-tokenizer@<first 12 hex digits of the locked tokenizer.json sha256>`.

If the loaded models, chunkers or recipe do not match the manifest, xat-rag refuses to use the index. It returns `index_mismatch_error` (409) for each route that uses that RAG. The `xat_rag_version` can differ, because it does not change the index. The app starts a rebuild. xat-rag never embeds again in silence. See [ADR 0005](adr/0005-sqlite-store-and-index-manifest.md).

### Code-aware lexical tokens

`Fts5Retriever` prepares the text before it inserts it into FTS5. It keeps the full compound and it adds its parts:

- `com.shop.OrderNotFoundException` gives `com_shop_ordernotfoundexception`, `com`, `shop`, `ordernotfoundexception`, `order`, `not`, `found` and `exception`.
- `snake_case` gives `snake_case`, `snake` and `case`. `camelCase` gives `camelcase`, `camel` and `case`.
- A SHA such as `4f2a9c1` stays one token.
- A JIRA key such as `PROJ-123` gives `proj_123`, `proj` and `123`. The `jira_key` exact lookup finds the issue.

The version of these rules is in the manifest, because a change needs a rebuild.

The Python `sqlite3` module cannot register a custom FTS5 tokenizer. Thus, xat-rag expands the text before insertion. FTS5 uses `unicode61` on the result, with the underscore as a token character. The same expansion runs on the query terms.

Query terms come from untrusted log lines. Characters such as `-` and `:` are operators in the FTS5 query syntax. Thus, xat-rag puts each token in double quotes, with inner quotes doubled. It joins the tokens with `OR`. It never passes raw text to `MATCH`.

`ExactLookupRetriever` and all filters bind each value as an SQL parameter. Values from log lines never become SQL text. The vector search adds a filter as a `rowid IN` condition inside the nearest-neighbor query, before the top-k cut. xat-rag loads only the locked sqlite-vec file. It calls `enable_load_extension(False)` after that load.

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
        --> generator token count (stored with the chunk)
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

## 6. Query and compose flow

```
caller --POST /v1/context {context: "log-triage", log_lines: [...]}-->

 1  Query builder (text-v1 or log-v1) --> RetrievalQuery and the input section
 2  For each RAG in the context recipe (filters apply inside each retriever):
       ExactLookupRetriever ----------------------------------+
       Fts5Retriever (lexical_k, default 40) ----------------+--> RRF (rrf_k, default 60)
       VecRetriever (dense_k, default 40) -------------------+    --> top rerank_candidates (40)
 3  Cross-encoder reranks the candidates with rerank_text (if the RAG has a reranker)
 4  Remove duplicates (same content hash, or cosine similarity of 0.95 or more)
    --> MMR (lambda 0.7) picks `keep` chunks for each RAG
 5  Budget for chunks (section 7)
 6  Packer: quotas for each source, exact fit with the token counter
 7  Order: strongest first, second strongest last. Render with labels C1 to Cn.

caller <-- ContextPack
```

- All RAGs in one context must use the same reranker, so that their rerank scores can be compared. xat-rag refuses a context recipe that breaks this rule.
- The reranker keeps all candidates of its RAG in score order. MMR then chooses the chunks that go on.
- The same query is embedded once for each RAG. A context with three RAGs embeds it three times.
- Blocking work (ONNX Runtime and SQLite) runs in a pool of two worker threads, never on the event loop.
- A request that takes too long fails with `stage_timeout_error` (504). xat-rag checks each stage limit after the stage ends. A stage is not stopped in the middle. This is a known limit.
- At most `XatRag__QueueLimit` requests can wait or run in the pool. More requests get `overloaded_error` (503, `Retry-After: 1`).
- Ties in a score are broken by `chunk_id`. Thus, the same input gives the same pack on one platform. ONNX scores can differ a little between CPUs, so the determinism test runs on each platform separately.

### Query builders

`TextQueryBuilder` (`text-v1`) takes a question. It keeps each term once, without case, and it keeps code names whole. It strips the punctuation at the edges of a term.

`LogQueryBuilder` (`log-v1`) is deterministic. It does these steps:

1. Cut each line to at most 4096 characters. Remove ANSI codes and control characters. Mask timestamps, UUIDs, hex values, SHAs, IP addresses and numbers. The masks use regular expressions that run in linear time.
2. Group equal masked lines into templates. Each template has a count and one raw example.
3. Extract identifiers: exception class names, JIRA keys, `file:line` stack frames, error codes, endpoint paths and SHAs.
4. Map the file name of each stack frame to a `path_name` lookup. The lookup uses the lowercase file name, because a stack frame rarely has the full repository path.
5. Fill the parts of `RetrievalQuery`.

A request has at most 2000 log lines. `POST /v1/query/build` returns the query, so that a person can check it.

## 7. Context budget model

See [ADR 0007](adr/0007-context-budget-and-token-counting.md).

- **The context recipe owns the window size and the split.** A request can lower the values, but not raise them. A request that sets a value above the recipe gets 400 `invalid_request_error`.
- **The window must match the generator.** It must equal the context size that the generator has for one request. The app must keep the recipe window and the generator context size equal.
- **A generator profile describes the model.** The profile has `context_window`, `tokenizer_id`, `template_overhead`, `per_message_overhead` and `safety_margin_pct` (3%). v1 has one profile, `ministral-3-3b`. Its values are `context_window` 8192, `template_overhead` 2 and `per_message_overhead` 3.
- **The overheads are measured, not guessed.** `tools/measure_profile.py` encodes sample chats with `mistral-common` 1.13.1, Mistral's reference tokenizer and chat-template library. The tool needs no generator and no model weights.
- **The counter uses the locked tokenizer.** It counts text without the special tokens, but `encode_special_tokens` is on. A text such as `[INST]` in a document is counted as text.
- **The budget for chunks** is:

  `window - reserved_output - template_overhead - per_message_overhead x 2 - renderer_text - pinned - input - margin`

  The two messages are the system message and the user message. `renderer_text` is the trust note and the separators of the renderer. The margin is rounded up: `ceil(window x 3%)`, so 246 tokens for 8192.
- **The packer counts each chunk as it is rendered**, with its `<chunk ...>` wrapper. Thus, the wrappers are inside the budget.
- **Pinned items** are prompts that the recipe names by ID. xat-rag counts them exactly, and it never prunes or moves them. If the pinned items and the input do not fit, the result is `context_too_large_error` (422). xat-rag never cuts a pinned item.
- **The input section** (log lines or a question) has its own cap, `input_max_tokens`. The default is 1200. xat-rag gives each template a rank, and it drops the templates with the lowest rank first:
  1. Templates from the lines that the user selected.
  2. Templates with level `ERROR`, `SEVERE`, `FATAL`, `CRITICAL` or `PANIC`, or with an identifier.
  3. Templates with level `WARN`.
  4. Other templates. Rare templates come before frequent ones.

  The kept lines keep their log order. A frequent template shows its count, for example `[x12]`. If the best template alone is larger than the cap, xat-rag cuts its raw example at a character position. The pack reports `dropped_templates` and `truncated`.
- **Each source has a quota.** Each RAG has `min_tokens`, a floor when it has hits, and `max_share`, the largest share of the budget. Tokens that a source does not use go back to the pool, in score order. If the budget is smaller than the sum of the floors, xat-rag lowers all floors in proportion. The pack then reports `floors_lowered`.
- **The pack gives `max_tokens`**, which is equal to `reserved_output`. The app must send this value to the generator.
- **Order in the prompt:**
  1. The system message: the pinned prompts, then the trust note.
  2. The user message: the chunk blocks, the strongest first and the second strongest last.
  3. The user message: the input section and the question, at the end.

## 8. The context pack

See [ADR 0008](adr/0008-context-pack-and-citations.md).

```json
{
  "pack_id": "p_3f9c2a7d1e4b8c60a1f2",
  "generator": "ministral-3-3b",
  "max_tokens": 768,
  "budget": {"window": 8192, "reserved_output": 768, "overhead": 240, "pinned": 310,
             "input": 640, "chunks": 4120, "margin": 246, "unused": 1868, "floors_lowered": false},
  "pinned": [{"id": "prompt:log-triage-system@3", "tokens": 310, "text": "..."}],
  "chunks": [{"label": "C1", "chunk_id": "jira:PROJ-123.json#desc", "doc_id": "jira:PROJ-123.json",
              "rag": "jira", "kind": "description", "uri": "...", "title": "...", "score": 0.91,
              "span": {"type": "line", "line_start": 1, "line_end": 14}, "tokens": 220, "text": "..."}],
  "input": {"text": "...", "tokens": 640, "dropped_templates": 2, "truncated": false},
  "rendered": {"messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]},
  "citation": {"grammar": "<GBNF>", "json_schema": {}},
  "omitted": [{"chunk_id": "...", "reason": "budget"}],
  "debug": null
}
```

- `rendered.messages` uses the OpenAI message format.
- Retrieved chunks and the input section are untrusted. They go only in the user message, inside `<chunk id="C1" kind="jira" trust="untrusted">...</chunk>` and `<input trust="untrusted">...</input>`.
  - xat-rag builds the attributes only from the label and the source kind, which it validates. Titles and URIs go inside the block, not into attributes.
  - Inside a block, xat-rag replaces `<` with `&lt;`. So no text can close or open a block.
  - A control-token pattern such as `[INST]` or `[/INST]` gets a space after the bracket (`[ INST]`, `[/ INST]`). Thus, the generator cannot read it as a control token.
- The answer format is `{"answer": "...", "citations": ["C1", "C3"]}`. The GBNF grammar allows only the labels in the pack. The app sends `citation.grammar` to the local model. It sends `json_schema` only to a generator that does not accept a grammar, and never both.
- The `uri` of a PDF chunk ends with `#page=N`, the standard PDF open parameter.
- xat-rag stores no packs. `POST /v1/citations/validate` takes the answer text and the `chunks` list (label and `chunk_id`) from the pack that the app keeps. It checks that the answer is valid JSON, that each label is in the list, and that each cited chunk still exists. It returns the URI of each citation.
- An answer that the generator cut at `max_tokens` is not valid JSON. The result is `valid: false` with the problem `not_json`.
- `omitted` gives the reason for each dropped candidate: `budget`, `quota` or `duplicate`.
- `debug` is filled only when the request sets `debug: true`. It holds the lexical rank, dense rank, exact rank, RRF score, rerank score and `rerank_truncated` of each candidate.

## 9. HTTP service

See [ADR 0003](adr/0003-library-and-loopback-service.md). The service accepts only loopback connections. All routes are under `/v1`, except the health routes. [The API reference](api.md) lists the bodies, the responses and the errors.

| Route | Purpose |
|---|---|
| `GET /health/live` | Live. It needs no access key. |
| `GET /health/ready` | Ready, with the version and the RAGs that need a rebuild. Degraded after a `parse_timeout`. |
| `GET /v1/rags` | The RAGs, their counts, and the names of the contexts. |
| `GET /v1/rags/{rag}` | The counts and the manifest of one RAG. |
| `PUT /v1/rags/{rag}/documents` | Start an ingestion job. The body is `multipart/form-data`. Returns 202 and a `job_id`. |
| `GET /v1/jobs/{job_id}`, `DELETE /v1/jobs/{job_id}` | The progress and the results. `DELETE` cancels the job before its next document. |
| `DELETE /v1/rags/{rag}/documents/{doc_id}` | Delete one document and its chunks. The `doc_id` is percent-encoded. |
| `POST /v1/rags/{rag}/rebuild` | Delete the index of the RAG and make an empty one. The app sends the documents again. |
| `POST /v1/rags/{rag}/search` | Ranked candidates with the scores of each stage. For debugging and evaluation. |
| `POST /v1/context` | Make a context pack. This is the main route. |
| `POST /v1/query/build` | Show the query that the builder makes from the input. |
| `POST /v1/tokens/count` | Count tokens with a generator profile. |
| `GET /v1/prompts/{name}/{version}` | Get a pinned prompt. Read only. |
| `POST /v1/citations/validate` | Validate an answer against the chunks of a pack. |

RAG names and prompt names match `[a-z0-9][a-z0-9-]{0,63}`.

### Access control

Loopback alone is not enough. Other programs and other users on the machine can connect to `127.0.0.1`, and a web page can try to reach it through the browser. Thus, the service has these rules:

- **An access key.** The app makes a random key of at least 16 ASCII characters at each start, and it gives the key in `XatRag__AccessKey`. Each request, except `GET /health/live`, must send `Authorization: Bearer <key>`. A wrong or missing key gets 401 `authentication_error`.
- **A host check.** The `Host` header must name `127.0.0.1`, `localhost` or `[::1]`, with or without a port. The check ignores case. This stops DNS rebinding.
- **No browser access.** The service sends no CORS headers. It refuses each request that has an `Origin` header.
- **A Unix socket** on macOS and Linux, with `XatRag__Urls=http://unix:<path>`. The file permissions of the socket decide who can connect. The socket is `0600`. The `Host` check still applies, so a client sends a loopback host name.

### Pinned prompts

Pinned prompts are files in the folder that `XatRag__PromptsFolder` names. A prompt file has the name `<name>@<version>.md` or `<name>@<version>.txt`. The app installs this folder, and the service reads it at startup. The HTTP API can only read prompts. Thus, only the app can add or change a pinned prompt. A prompt file is at most 256 KiB, and it must be UTF-8.

Prompt files can also go into a RAG with the `prompt` source kind. Such prompts are retrieved text, and they are untrusted like all other chunks.

### Lifecycle

The service has this lifecycle:

- `XatRag__Urls=http://127.0.0.1:0` lets the system choose a free port.
- The service loads the recipes, the models and the indexes before it accepts requests.
- `XatRag__ReadyFile` holds `{url, pid, version}` when the service is ready. A stale file from an earlier run is deleted at start.
- `XatRag__StopWhenInputCloses=true` stops the service when its standard input closes. `SIGINT` and `SIGTERM` also stop it, and the cleanup runs.
- `XatRag__DataFolder` holds the indexes, the lock file `xat-rag.lock` and the model check cache. The folder has mode `0700`. A lock allows one service for each data folder.
- `XatRag__QueueLimit` sets the number of requests that can wait for the worker pool.

A startup failure stops the service. A bad recipe, a bad model or a bad setting gives a message and exit code 2 or another non-zero code.

## 10. Recipes

Recipes are TOML files. xat-rag reads them with `tomllib` and validates them with pydantic. An unknown key is an error. The `format` field gives the version of the file format, and it is 1.

A **RAG recipe** describes one index:

```toml
# rags/jira.toml
[rag]
name = "jira"
format = 1
embedder = "bge-small-en-v1.5"
reranker = "ms-marco-minilm-l6-v2"   # Optional. The default is "none".
generator = "ministral-3-3b"         # Optional. Its tokenizer counts max_chunk_tokens.

[sources.main]
kind = "jira"                        # markdown, text, diff, jira, prompt, pdf, docx or html
max_chunk_tokens = 350               # 32 to 2000. The default is 350.
min_page_chars = 20                  # PDF only. The default is 20.

[retrieval]                          # These values do not change the index.
lexical_k = 40
dense_k = 40
exact_k = 20
rrf_k = 60
rerank_candidates = 40
keep = 8
mmr_lambda = 0.7
```

The name of a source table, such as `[sources.main]`, is free. A RAG can have one source for each kind.

A **context recipe** combines RAGs, a budget and a query builder:

```toml
# contexts/log-triage.toml
[context]
name = "log-triage"
format = 1
generator = "ministral-3-3b"
window = 8192
reserved_output = 768
input_max_tokens = 1200
pinned = ["prompt:log-triage-system@3"]
query_builder = "log-v1"             # log-v1 or text-v1

[[context.sources]]
rag = "docs"
min_tokens = 500
max_share = 0.40

[[context.sources]]
rag = "jira"
min_tokens = 400
max_share = 0.35

[[context.sources]]
rag = "diffs"
min_tokens = 400
max_share = 0.35

[limits]                             # Milliseconds for each stage of a context request.
query_build = 200
retrieve = 500
embed = 1000
rerank = 2500
pack = 300
```

`order` and `citation` are also fields of the context. Each takes one value in v0.1: `edges` and `json-gbnf`.

xat-rag refuses these recipes at startup, with a message that names the problem:

- A context that names a RAG twice.
- A context with a `window` above the limit of its generator.
- A context that pins a prompt that is not in the prompts folder.
- A RAG with two sources of the same kind.
- A recipe file that is not UTF-8.
- Two prompt files that give the same prompt ID.
- A context that names an unknown RAG, or RAGs with different rerankers.
- A file name that does not match the recipe name.

Apps make new RAG setups by writing recipes, not code. Service settings use environment variables with the prefix `XatRag__`. Ingestion jobs have no stage limits, because their time depends on the batch size. The caller can cancel a job.

## 11. Errors

See [ADR 0009](adr/0009-error-model.md). Each kind of failure has one error type and one status code. xat-rag never retries. There is no silent fallback. All errors use one body: `{"error": {"message", "type"}}`.

| Error type | Status | Example |
|---|---|---|
| `invalid_request_error` | 400, 413 | A bad body or field, a bad upload, a body that is too large, or a rebuild while a job runs. |
| `authentication_error` | 401 | The access key is missing or wrong. |
| `not_found_error` | 404 | An unknown RAG, document, job, context, prompt or route. |
| `index_mismatch_error` | 409 | The manifest does not match the models, chunkers or recipe. Rebuild the RAG. |
| `context_too_large_error` | 422 | The pinned items and the input do not fit in the window. |
| `service_unavailable_error` | 503 | The service is degraded after a `parse_timeout` (no `Retry-After`, the app must restart the service). Or a RAG is being rebuilt or stopped (`Retry-After: 1`). |
| `overloaded_error` | 503 | The request pool or the upload queue is full. Has `Retry-After`. |
| `stage_timeout_error` | 504 | A stage took longer than its limit. The message names the stage. |
| `parse_error` | In the result of one document | One document did not parse. The `reason` is one of `invalid`, `encrypted`, `no_text_layer`, `unsupported_media_type`, `too_large`, `limit_exceeded` or `parse_timeout`. |
| `internal_error` | 500 | An unexpected failure, or a failed stage such as the reranker. The response has no details. |

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
- `build/models.lock.json` lists each model with its repository, revision and license. It lists each file of each model with its size and sha256.
- `tools/fetch_models.py` gets the files at build time only. xat-rag never downloads at runtime.
- At startup, xat-rag checks the size and sha256 of the files that the runtime uses: `model.onnx` and `tokenizer.json` of each ONNX model, and `tokenizer.json` of the generator. The `tekken.json` file is for development only. The check result is cached in `model-check-cache.json` in the data folder. The cache key is the path, the size, the modification time, the inode and the sha256.
- A program that can write to the data folder can also change the cache. The app must protect the data folder (section 14).
- xat-rag loads tokenizers only from local files (`Tokenizer.from_file`). It never calls `from_pretrained`, which can download.
- The generator tokenizer for Ministral-3-3B is a locked file too.

## 13. Evaluation

The `xat-rag-eval` package does these tasks:

- It reads golden sets in JSONL. Each line is one case. The fields are `id`, `context`, `rag`, `kind`, `question`, `log_lines` and `relevant`. Unknown fields are errors. A case needs a question or log lines, and at least one relevant item. Each item has exactly one of `chunk_id` or `doc_id`, and a `grade` from 1 to 3. A case scores chunks or documents, not both. Case IDs are unique.
- Each case measures only the candidates of its `rag`. Ranks and RRF scores are per RAG.
- It reports recall@10, nDCG@10 and MRR after each stage: lexical, dense, exact, fused and reranked. The reranked stage is the order that the packer gets, after the reranker, the duplicate removal and MMR.
- It reports **pack recall**: the share of relevant chunks inside the final pack.
- It checks two rules: the prompt never exceeds the window minus the reserved output and the safety margin, when the rendered messages are counted again, and the same input gives the same pack.
- It reports p50 and p95 times for the retrieval and for the context composition.
- It fails closed. The baseline file is required, unless the run writes it. The `--data` folder must be new or empty. A kind that is in only one of the report and the baseline fails. The reranked recall@10 and the pack recall of each kind are gated. A drop of more than 2 points (200 basis points) fails. A drop of exactly 2 points passes.
- `--write-baseline` refuses to write when the budget or determinism problems exist. It writes only the kinds, without the timings.

The CLI is `xat-rag-eval run --setup <folder> --golden <file> --data <new or empty folder> --baseline <file>`.

The fixture set in `eval/fixtures` has three RAGs (docs, JIRA and diffs) and a corpus of Markdown, JIRA JSON and unified diffs. It has no PDF, DOCX or HTML golden cases yet.

A **chunk-boundary test** (`packages/xat-rag-eval/tests/test_chunk_boundaries.py`) compares the chunk IDs, spans and content hashes of the fixture corpus with `eval/chunk_boundaries.json`. An upgrade of `langchain-text-splitters` or of a parser library that moves a boundary fails this test. After a reviewed change, the file is written again with `XATRAG_WRITE_BOUNDARIES=1`. The release notes must then say that a rebuild is needed.

Known limit: one `eval/baseline.json` is shared by the three operating systems. The int8 ONNX numerics can differ a little by CPU and by batch. A per-system baseline can be needed later.

Ablation runs (no lexical search, no dense search, no reranker) are manual in v1. The harness calls no generator, so it uses no judge model.

## 14. Privacy and untrusted content

See [ADR 0010](adr/0010-untrusted-content-privacy-and-ingestion.md) and [ADR 0011](adr/0011-offline-hardening.md).

### Offline hardening

`langchain-core` ships the `langsmith` client. A test showed that `LANGSMITH_TRACING=true` in the environment makes one retriever call send data to `api.smith.langchain.com`. Inside `tracing_context(enabled=False)`, the same call sent nothing. [ADR 0011](adr/0011-offline-hardening.md) gives the full rules. In short:

1. **Service mode.** A PyInstaller runtime hook calls `harden_environment` before `main`. It deletes all `LANGSMITH_*` and `LANGCHAIN_*` variables, and it sets the tracing variables to `false`. It sets the Hugging Face offline variables and `HF_HOME` in the data folder. It removes variables that end in `_API_KEY`, `_TOKEN`, `_ENDPOINT` or `_ENDPOINTS`, except `XatRag__*` names. It keeps `XatRag__AccessKey` for `main`. The service entry point then calls `xat_rag_offline.harden_process()` first. That call reads the access key once, removes it from the environment, and installs the network guard. A second `XatRag__AccessKey` variable that differs only in case is refused.
2. **Service mode.** After the imports, the service calls `langsmith.configure(enabled=False)`, and it turns off LangChain debug and verbose output.
3. **Both modes.** Each use case turns tracing off inside its worker thread, for its own calls. In library mode, this is the only protection. xat-rag does not change the process of the host app.
4. **Service mode.** An audit hook refuses these events:
   - Network events (`socket.connect`, `socket.bind`, `socket.sendto`, `socket.sendmsg`, the name lookups and `socket.getnameinfo`) for any address that is not loopback. A send with no address is allowed.
   - Process events (`subprocess.Popen`, `os.system`, `os.exec*`, `os.spawn*`, `os.posix_spawn`, `os.fork` and `os.forkpty`). These are always refused.
   - `ctypes.dlopen`. The service allows no library.
   - `sqlite3.load_extension`, except the locked sqlite-vec file.

   The hook is defense in depth only. Native code can pass it.
5. **CI.** The `offline` job runs the service and library tests in a new network namespace (`sudo unshare --net`), and it brings the loopback interface up. Any other connection fails. The job sets `LANGSMITH_TRACING`, `LANGSMITH_API_KEY` and `LANGSMITH_RUNS_ENDPOINTS`. The job uses no packet capture yet.

The service depends on `opentelemetry-api`, because FastAPI requires it. xat-rag does not configure it. It has no exporter and no SDK, so it sends nothing. The package is on the dependency allowlist.

### Logs and telemetry

- The service code logs only the class name of an unexpected exception. It logs no document IDs, chunk IDs, text, queries or log lines.
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
- The tests check that deleted lexical text is gone from the files. They do not check the shadow tables of sqlite-vec yet. Vectors can leak text, so this check is an open item (section 18).
- Software cannot remove all copies. SSD wear leveling, file system journals, backups and snapshots can keep old data. To remove a whole RAG, delete its file. The docs of the app must tell this to its users.

### Personal data

- Author and assignee values are metadata only. They do not go into the chunk text or into the rendered messages.
- Free text can still hold names, for example commit messages, `Signed-off-by` lines and mentions in comments. xat-rag does not find these. The app must remove them before it sends the documents, if its policy requires it.

### Untrusted content

- Sanitizing runs on the text that the parser extracts, before chunking. It removes ANSI codes and control characters, except tab, line feed and carriage return. It removes zero-width characters, bidirectional controls (U+202A to U+202E and U+2066 to U+2069), Unicode tag characters (U+E0000 to U+E007F) and variation selectors. It turns NEL and the line and paragraph separators into line feeds. Then it normalizes the text to NFC.
- Retrieved chunks and the input section (log lines and the question) are untrusted. Log lines can hold text from an attacker. Both go inside escaped blocks (section 8). The system message tells the model to treat the blocks as data.
- Delimiters reduce the risk of prompt injection, but they do not remove it. Citation validation checks only the labels. The app must treat the answer as untrusted text. It must not run commands or open links from the answer without a user action.
- Only the app can add pinned prompts (section 9).

## 15. Extension points

v1 has no plugin loading. A change to one of these parts needs a change to the code:

- **A new source kind.** Add the kind to the `SourceKind` enum, to the recipe `SourceKindName`, and to `make_parser` in `xat_rag/index.py`. Write a parser that returns sections, and a chunker rule if the kind needs one. The parser must not read files or the network. A new kind may need a new library. Such a library must pass the dependency allowlist.
- **A new query builder.** Add a class with a `name` and a `build` method, and add the name to the `query_builder` literal in the recipe.
- **A new embedder or reranker.** Add the model to the lock, and add a spec to `EMBEDDERS` or `RERANKERS` in `xat_rag/models.py`. Each model needs an ONNX export and a check.
- **A new store.** The store is `SqliteRagStore`. The retrievers use it directly. A second store needs a new retriever set, and it is a larger change.
- **LangChain users.** A `xat_rag.langchain` module is planned. v0.1 does not have it. The retrievers, the embedder and the reranker are LangChain objects in the packages, and an app can import them.
- **A new render template.** The renderer has a version constant, `render-v1`. The constant is not in the manifest. A change to the template changes the budget text, so the change needs a test and a release note.

## 16. Requirement map

| Requirement | Where |
|---|---|
| FR-1, FR-2, FR-3 | Sections 4 and 5, `xat-rag-parsers`, `xat-rag-documents`, [ADR 0012](adr/0012-document-parsers.md) |
| FR-4, FR-5 | Sections 4 and 5 |
| FR-6, FR-7, FR-8, FR-9 | Section 6, [ADR 0006](adr/0006-hybrid-retrieval-and-reranking.md) |
| FR-10 | v1.1, the planned `Pruner` port |
| FR-11 | Section 7 |
| FR-12 | Section 6, LogQueryBuilder |
| FR-13, FR-14 | Section 8 |
| FR-15 | Section 13 |
| FR-16 | Section 4, index manifest |
| NFR-1, NFR-2, NFR-3, NFR-11 | Sections 12 and 14, [ADR 0011](adr/0011-offline-hardening.md) |
| NFR-4 | Section 9 |
| NFR-5 | Section 11 |
| NFR-6 | [ADR 0005](adr/0005-sqlite-store-and-index-manifest.md) |
| NFR-7, NFR-8, NFR-9, NFR-14, NFR-15 | Section 13 measures them on the hardware floor. |
| NFR-10 | Section 5 |
| NFR-12 | [ADR 0002](adr/0002-python.md) |
| NFR-13 | [Writing style](writing-style.md) |
| Defaults | Section 17 |

## 17. Defaults

The maintainer decided these v1 defaults on 2026-11-07. A recipe can change the recipe values. The service settings can change the limits.

| Area | Default |
|---|---|
| Budget | 768 tokens for the answer, at most 1200 tokens for the input section, a 3% safety margin |
| Quotas in `log-triage` (example recipe) | docs: 500 tokens and 40%. jira: 400 tokens and 35%. diffs: 400 tokens and 35%. |
| Retrieval | Top 40 lexical, top 40 dense and top 20 exact. RRF with k=60. Rerank 40 candidates. Keep 8 for each RAG. MMR lambda 0.7. |
| Chunks | 350 generator tokens for each chunk, from 32 to 2000 |
| Upload | 64 MiB and 256 documents for each request. 4 MiB for each text document, 8 MiB for each HTML document, 32 MiB for each PDF or DOCX document. 256 MiB of queued upload data for the service. |
| Context request body | 1 MiB |
| Log input | At most 2000 lines for each request, and 4096 characters for each line |
| PDF | At most 2000 pages, `min_page_chars` = 20, 200k characters for each page, 20M for each document |
| DOCX | At most 10k zip entries, 256 MiB uncompressed, 64 MiB for `word/document.xml`, a compression ratio of 100 |
| HTML | At most 8 MiB and 500k `<` characters |
| Parse deadline | 60 seconds for each document. A watchdog marks the service degraded after 420 seconds. |
| Stage time limits | Query build 200 ms, retrieve 500 ms, embed 1000 ms, rerank 2500 ms, pack 300 ms |
| Service | Two worker threads, a queue limit of 8 requests, and ONNX Runtime threads chosen by ONNX Runtime |
| Upgrades | A monthly batch. Security fixes (for example pypdf) at once. |

## 18. Later versions and open items

Later versions:

- Chunk code files by syntax tree (tree-sitter).
- OCR for scanned PDF pages, table structure in PDF, and encrypted PDFs.
- The `xat_rag.langchain` module, with a LangChain `VectorStore` adapter.
- Plugin loading for source kinds and query builders.
- Neighbor expansion of chunks inside the budget.
- Sentence pruning for prose sources.
- Multilingual embedder and reranker as recipe options.
- The full Drain algorithm for log templates.
- Ablation runs in CI.
- Token profiles for cloud generators.
- Deterministic links between sources, such as a stack frame to a diff hunk or a JIRA key to a commit.

Open items in v0.1:

- The DOCX parser does not set the `lxml` options `resolve_entities=False` and `huge_tree=False`. The zip checks and the grid span check run before the parse.
- A test does not check that sqlite-vec overwrites deleted vectors. Section 14 gives the reason.
- The CI `offline` job uses a network namespace. It does not use a packet capture.
- The service has no setting for the parse deadline.
- One `eval/baseline.json` is shared by the three operating systems. A per-system baseline can be needed, because int8 ONNX numerics can differ a little by CPU and batch (section 13).
- The frozen folder is checked for denied modules by `check-bundle`. The check runs only in the package job of CI.
