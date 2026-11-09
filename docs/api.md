# API reference

This page lists the HTTP routes and the Python classes of xat-rag. To start the service, read the [quickstart](quickstart.md).

- [Conventions](#conventions)
- [Errors](#errors)
- [Routes](#routes)
- [Chunk fields](#chunk-fields)
- [Upload format](#upload-format)
- [Recipes](#recipes)
- [Settings](#settings)
- [Lifecycle](#lifecycle)
- [Limits](#limits)
- [Python library](#python-library)

## Conventions

The base URL is the `url` value in the ready file. All routes use JSON, except the upload route. RAG names and context names match `[a-z0-9][a-z0-9-]{0,63}`.

The service has no authentication. Each program on the same machine can use every route.

The service checks every request in this order:

1. The `Host` header must be `127.0.0.1`, `localhost` or `[::1]`, with or without a port. A Unix socket caller must also send one of these names.
2. The request must have no `Origin` header.

A request that fails a check gets 400 `invalid_request_error`. The service sends no CORS headers. The OpenAPI schema is at `/openapi.json`.

## Errors

Each error has this body:

```json
{"error": {"message": "The document does not exist.", "type": "not_found_error"}}
```

| Type | Status | Cause | Action |
|---|---|---|---|
| `invalid_request_error` | 400 | A body, field, upload or query parameter is not valid. A question has no text after sanitizing. A rebuild is requested while a job of the RAG runs. | Correct the request. The message names the field. |
| `invalid_request_error` | 405 | A route is used with the wrong method. | Correct the method. |
| `invalid_request_error` | 413 | A body is larger than its limit. | Send less data. |
| `not_found_error` | 404 | An unknown route, RAG, document, job or context. | Correct the name. |
| `index_mismatch_error` | 409 | The index does not match the models or the recipe. | Rebuild the RAG and upload its documents again. |
| `service_unavailable_error` | 503 | With no `Retry-After`, the service is degraded. With `Retry-After: 1`, the RAG is being rebuilt or the service is stopping. | Restart the service, or wait. |
| `overloaded_error` | 503 | The request pool is full (`Retry-After: 1`), or the upload queue is full (`Retry-After: 5`). | Wait, and send the request again. |
| `stage_timeout_error` | 504 | A stage passed its time limit. The message names the stage. | Send the request again, or increase the limit in the context recipe. |
| `internal_error` | 500 | An unexpected failure, or a failed stage such as the reranker. | Report the problem. The body has no details. |

xat-rag never retries. Error messages never repeat an input value.

## Routes

| Method and path | Purpose |
|---|---|
| [`GET /health/live`](#get-healthlive) | Check that the process runs. |
| [`GET /health/ready`](#get-healthready) | Check that the service is ready. |
| [`GET /v1/rags`](#get-v1rags) | List the RAGs and contexts. |
| [`GET /v1/rags/{rag}`](#get-v1ragsrag) | Show the counts and the manifest of a RAG. |
| [`POST /v1/rags/{rag}/rebuild`](#post-v1ragsragrebuild) | Delete an index and make an empty one. |
| [`PUT /v1/rags/{rag}/documents`](#put-v1ragsragdocuments) | Start an ingestion job. |
| [`DELETE /v1/rags/{rag}/documents/{doc_id}`](#delete-v1ragsragdocumentsdoc_id) | Delete one document. |
| [`GET /v1/jobs/{job_id}`](#get-v1jobsjob_id) | Show a job. |
| [`DELETE /v1/jobs/{job_id}`](#delete-v1jobsjob_id) | Ask a job to stop. |
| [`POST /v1/context`](#post-v1context) | Return the ranked chunks for a question. |
| [`POST /v1/rags/{rag}/search`](#post-v1ragsragsearch) | Return the candidates of one RAG, for debugging. |
| [`POST /v1/query/build`](#post-v1querybuild) | Show the query that a question makes. |

### `GET /health/live`

Returns 200 and `{"status": "live"}`.

### `GET /health/ready`

Returns 200 when the service is ready:

```json
{"status": "ready", "version": "0.1.0", "needs_rebuild": []}
```

`needs_rebuild` lists the RAGs whose index does not match. Only these RAGs give 409.

The route returns 503 when a document ran too long and the service is degraded:

```json
{"status": "degraded", "reason": "parse_timeout"}
```

### `GET /v1/rags`

```json
{"rags": [
   {"name": "docs", "needs_rebuild": false, "documents": 3, "chunks": 21},
   {"name": "jira", "needs_rebuild": true}
 ],
 "contexts": ["ask-docs", "triage"]}
```

### `GET /v1/rags/{rag}`

```json
{"name": "docs", "documents": 3, "chunks": 21,
 "manifest": {"index_format": 1, "embedder": "bge-small-en-v1.5", "embedder_sha256": "...",
              "dims": 384, "lexical_tokenizer": "code-v1",
              "size_tokenizer_id": "ministral-3-3b-tokenizer@99cf274236c6",
              "index_hash": "...", "chunkers": {"markdown": "markdown-v2+lcts1.1.3"}}}
```

### `POST /v1/rags/{rag}/rebuild`

Deletes the index of the RAG and makes an empty one. Returns `{"rebuilt": true, "documents": 0}`. The caller then uploads all the documents again. The route returns 400 while a job of the RAG is queued or running.

### `PUT /v1/rags/{rag}/documents`

Starts an ingestion job. The body is `multipart/form-data`. See the [upload format](#upload-format). Returns 202:

```json
{"job_id": "j_0123456789abcdef0123"}
```

A bad document does not give an HTTP error. It gives a `failed` result in the job.

### `DELETE /v1/rags/{rag}/documents/{doc_id}`

Deletes one document and its chunks. Percent-encode the `doc_id`. Returns `{"deleted": true}`, or 404 when the RAG does not have the document.

### `GET /v1/jobs/{job_id}`

```json
{"job_id": "j_0123456789abcdef0123", "rag": "docs", "status": "done", "total": 1, "done": 1,
 "error_type": null, "notes": [],
 "results": [
   {"doc_id": "docs:orders.md", "status": "created", "chunks": 2, "embedded": 2,
    "error_type": null, "reason": null, "message": null,
    "pages_total": null, "pages_indexed": null, "skipped_pages": [], "notes": []}
 ]}
```

| Field | Values |
|---|---|
| `status` of a job | `queued`, `running`, `done`, `cancelled` or `failed` |
| `done` | The number of results that exist. |
| `error_type` of a job | Set when the job fails. |
| `notes` of a job | Can hold `checkpoint_failed:<error type>`. |
| `status` of a result | `created`, `updated`, `unchanged` or `failed` |
| `pages_total`, `pages_indexed`, `skipped_pages` | PDF only. Each skipped page has `page`, `label` and `reason`. The reason is `no_text`, `extract_error` or `page_text_limit`. |
| `notes` of a result | `binary_file_skipped`, `file_without_hunks_skipped`, `comments_skipped`, `headers_footers_skipped` or `tracked_insertions_kept` |

A failed result has an `error_type`, a `reason` and a `message`. The reasons of `parse_error` are these:

| Reason | Cause |
|---|---|
| `invalid` | The bytes are not the kind of document, or the document has no text. |
| `encrypted` | The PDF is encrypted. |
| `no_text_layer` | The PDF has no page with text. |
| `unsupported_media_type` | The DOCX file has macros. |
| `too_large` | The document is larger than its limit. |
| `limit_exceeded` | A parser limit was passed. The message names the limit. |
| `parse_timeout` | The parse deadline passed. |

Jobs live in memory, so a restart loses them. The service keeps the last 200 finished jobs.

### `DELETE /v1/jobs/{job_id}`

Asks the job to stop before its next document, and returns the job at once. A job that is done or failed does not change.

### `POST /v1/context`

Returns the ranked chunks for a question. This is the main route.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `context` | string, 1 to 64 characters | Required | The name of a context recipe. |
| `question` | string, at most 20000 characters | Required | The question. It must have text after sanitizing. |
| `chunks` | JSON integer, 1 to 100 | `chunks` of the recipe | The highest number of chunks to return. |
| `debug` | boolean | `false` | Adds the scores of each candidate. |

```bash
curl -s -X POST "$URL/v1/context" \
  -H "Content-Type: application/json" \
  -d '{"context": "ask-docs", "question": "Which port does the orders service use?", "chunks": 5}'
```

```json
{"chunks": [{"chunk_id": "docs:orders.md#h/orders-service/ports-and-health", "...": "..."}],
 "omitted": [{"chunk_id": "docs:orders.md#h/orders-service/database-timeouts", "reason": "limit"}],
 "debug": null}
```

The items of `chunks` have the [chunk fields](#chunk-fields). The result follows these rules:

- `chunks` is in rank order, best first. A `score` is never higher than the score before it.
- `chunks` is an upper bound. Each RAG gives at most its `keep` chunks, so a result can be shorter than the request.
- `omitted` lists the candidates that were found but not returned. A candidate that `keep` removes is not listed. A chunk ID can appear more than once.
- An empty `chunks` list is a success. A `low_score` item in `omitted` means that each candidate scored below `min_score`. Otherwise, the RAGs hold no matching chunks.
- A chunk whose document is deleted during the request is left out, and it is not listed in `omitted`.
- A context with a RAG that needs a rebuild returns 409.

| `omitted` reason | Meaning |
|---|---|
| `duplicate` | The chunk repeats a better chunk, or its ID is already in the result. |
| `low_score` | The score is below the `min_score` of the context recipe. |
| `limit` | The result is full. |

`debug` is `null` unless the request sets it. When set, it is a list. Each item has `chunk_id`, `rag`, `lexical_rank`, `dense_rank`, `rrf_score`, `rerank_score` and `rerank_truncated`.

### `POST /v1/rags/{rag}/search`

Returns the candidates of one RAG, for debugging and evaluation. The `min_score` of a context does not apply. The body is `{"question": "..."}`, with at most 20000 characters.

```json
{"query": {"lexical_terms": ["port"], "dense_text": "port", "rerank_text": "port"},
 "hits": [{"chunk_id": "...", "score": 1.34, "...": "...",
           "lexical_rank": 1, "dense_rank": 1, "rrf_score": 0.0328, "rerank_score": 1.34}]}
```

Each hit has the [chunk fields](#chunk-fields) and these fields:

- `lexical_rank` and `dense_rank`: the 1-based rank in each search, or `null`.
- `rrf_score`: the score of the fusion.
- `rerank_score`: the score of the reranker, or `null` when the RAG has no reranker.

### `POST /v1/query/build`

Shows the query that a question makes. The body is the same as for search.

```json
{"lexical_terms": ["port"], "dense_text": "port", "rerank_text": "port"}
```

| Field | Content |
|---|---|
| `lexical_terms` | At most 32 unique terms. Code names stay whole. |
| `dense_text` | The whole question after sanitizing. The embedder uses only the part that fits its 512-token input. |
| `rerank_text` | The first 600 characters of the question. |

## Chunk fields

| Field | Type | Meaning |
|---|---|---|
| `chunk_id` | string | The stable ID of the chunk, as `<doc_id>#<anchor>`. |
| `parent_id` | string or `null` | A JIRA comment has the `desc` chunk of its issue as its parent. Otherwise `null`. |
| `kind` | string | `section`, `hunk`, `description`, `comment`, `prompt` or `page`. |
| `score` | number | The rerank score, or the RRF score when the RAG has no reranker. Higher is better. A rerank score is not a probability and has no fixed range. |
| `size_tokens` | integer | The size from the fixed size ruler of xat-rag. It is not a count of your LLM tokens. |
| `header` | string | The heading path or the title. Untrusted text. |
| `text` | string | The stored text, with no tags and no escaping. Untrusted text. |
| `source` | object | Where the chunk came from. |
| `metadata` | object | Parser fields and caller fields, with scalar values. Untrusted text. xat-rag does not filter on metadata. |

The `source` object has these fields:

| Field | Meaning |
|---|---|
| `rag` | The name of the RAG. |
| `doc_id` | The document ID that the caller sent. |
| `source_kind` | `markdown`, `text`, `diff`, `jira`, `prompt`, `pdf`, `docx` or `html`. |
| `uri` | The link that the caller sent, or an empty string. A PDF chunk ends with `#page=N`. |
| `title` | The title of the document. |
| `version` | The version that the caller sent, or an empty string. |
| `span` | The part of the document that holds the chunk. |

The `span` has one of three forms:

| `type` | Source kinds | Other fields |
|---|---|---|
| `line` | Markdown, text, diff, JIRA, prompt, HTML | `line_start`, `line_end` |
| `page` | PDF | `page_start`, `page_end`, `label_start`, `label_end` |
| `block` | DOCX | `block_start`, `block_end` |

## Upload format

The body of `PUT /v1/rags/{rag}/documents` has these parts:

- One part named `documents`. It holds a JSON list with one object for each document.
- One part for each file. The part names are unique in the request.

A part must not have a `Content-Transfer-Encoding` header.

| Field | Type | Required | Meaning |
|---|---|---|---|
| `doc_id` | string, 1 to 512 characters | Yes | The ID of the document, for example `docs:orders.md`. |
| `kind` | string | Yes | A source kind that the RAG has in its recipe. |
| `part` | string, 1 to 128 characters | Yes | The name of the file part. |
| `uri` | string, at most 2048 characters | No | A link to the source. |
| `title` | string, at most 1024 characters | No | The title. |
| `version` | string, at most 256 characters | No | A version, for example a commit SHA. |
| `metadata` | object of scalar values | No | Metadata for each chunk of the document. |

An unknown field is an error. An upload of a document with an existing `doc_id` updates the document. xat-rag embeds only the chunks that are new or changed.

```bash
curl -s -X PUT "$URL/v1/rags/docs/documents" \
  -F 'documents=[{"doc_id": "docs:orders.md", "kind": "markdown", "part": "f1", "uri": "orders.md"}];type=application/json' \
  -F 'f1=@orders.md'
```

## Recipes

Recipes are TOML files in `rags/*.toml` and `contexts/*.toml`. The file name must match the recipe name. An unknown key stops the start.

### RAG recipe

| Key | Default | Range | Changes the index |
|---|---|---|---|
| `rag.name` | Required | | |
| `rag.format` | `1` | `1` | |
| `rag.embedder` | Required | `bge-small-en-v1.5` | Yes |
| `rag.reranker` | `"none"` | `none`, `ms-marco-minilm-l6-v2` | No |
| `sources.<name>.kind` | Required | A source kind. One source for each kind. | Yes |
| `sources.<name>.max_chunk_tokens` | `350` | 32 to 2000 | Yes |
| `sources.<name>.min_page_chars` | `20` | 0 to 10000 | Yes |
| `retrieval.lexical_k` | `40` | 1 to 1000 | No |
| `retrieval.dense_k` | `40` | 1 to 1000 | No |
| `retrieval.rrf_k` | `60` | 1 to 1000 | No |
| `retrieval.rerank_candidates` | `40` | 1 to 500 | No |
| `retrieval.keep` | `8` | 1 to 100 | No |
| `retrieval.mmr_lambda` | `0.7` | 0 to 1 | No |

### Context recipe

| Key | Default | Meaning |
|---|---|---|
| `context.name` | Required | The name of the context. |
| `context.format` | `1` | The file format. |
| `context.chunks` | `10` | The default number of chunks, from 1 to 100. |
| `context.min_score` | None | A finite number. A chunk with a lower score is removed. The RAGs must have a reranker. |
| `[[context.sources]]` `rag` | Required | One table for each RAG. |
| `limits.query_build` | `200` | Milliseconds. |
| `limits.retrieve` | `500` | Milliseconds. |
| `limits.embed` | `1000` | Milliseconds. |
| `limits.rerank` | `2500` | Milliseconds. |
| `limits.pack` | `300` | Milliseconds for the merge of the RAG results. |

All RAGs of a context must use the same reranker. If they have no reranker, they must use the same `rrf_k`.

## Settings

The service reads environment variables. The names are not case-sensitive. In this table, `<release>` is the folder of the frozen executable.

| Variable | Default | Meaning |
|---|---|---|
| `XatRag__Urls` | `http://127.0.0.1:0` | `http://127.0.0.1:<port>`, `http://[::1]:<port>` or `http://unix:<path>`. Port 0 lets the system choose. Only loopback is accepted. |
| `XatRag__DataFolder` | `<release>/data` | The indexes, the lock file and the model check cache. |
| `XatRag__RecipesFolder` | `<release>/recipes` | The recipes. |
| `XatRag__ModelsFolder` | `<release>/models` | The model files. |
| `XatRag__LockFile` | `<ModelsFolder>/models.lock.json` | The model lock file. |
| `XatRag__ReadyFile` | `<DataFolder>/ready.json` | The file that holds `{url, pid, version}`. An empty value means no ready file. |
| `XatRag__StopWhenInputCloses` | `false` | `true` stops the service when its standard input closes. |
| `XatRag__QueueLimit` | `8` | The requests that can wait or run in the pool. From 1 to 1000. |
| `XatRag__Threads` | `0` | The ONNX Runtime threads, from 0 to 256. `0` lets ONNX Runtime choose. |

From source, no default applies to the data, recipes, models and lock file settings. They are required. A path setting with an empty value uses its default.

The service removes `LANGSMITH_*`, `LANGCHAIN_*` and each variable whose name ends in `_API_KEY`, `_TOKEN`, `_ENDPOINT` or `_ENDPOINTS`, except the `XatRag__*` variables.

## Lifecycle

The service starts in this order:

1. It reads its settings.
2. It takes the lock of the data folder. A second service with the same folder stops with exit code 2.
3. It deletes a stale ready file.
4. It loads the recipes, the models and the indexes.
5. It starts to listen.
6. It writes the ready file.

```json
{"url": "http://127.0.0.1:52341", "pid": 4312, "version": "0.1.0"}
```

A failed start prints one line that starts with `xat-rag:`, and it exits with code 2. A busy port is the exception, because it prints the message of uvicorn.

The service stops on `SIGINT`, on `SIGTERM`, or when standard input closes with `XatRag__StopWhenInputCloses=true`. It waits up to 30 seconds for a running document, and it deletes the ready file.

## Limits

| Limit | Value | Result when passed |
|---|---|---|
| JSON request body | 1 MiB | 413 |
| Upload request | 64 MiB | 413 |
| Documents in one upload | 256 | 400 |
| Queued upload data in the service | 256 MiB | 503 `overloaded_error` |
| Requests in the pool | `XatRag__QueueLimit` | 503 `overloaded_error` |
| `question` | 20000 characters | 400 |
| `chunks` | 1 to 100 | 400 |
| Stage time | See the `limits` keys of the [context recipe](#context-recipe). | 504 `stage_timeout_error` |
| Text document (`markdown`, `text`, `diff`, `jira`, `prompt`) | 4 MiB | `parse_error`, `too_large` |
| HTML document | 8 MiB | `parse_error`, `too_large` |
| PDF or DOCX document | 32 MiB | `parse_error`, `too_large` |
| Parse time of one document | 60 seconds | `parse_error`, `parse_timeout` |
| PDF pages | 2000 | `parse_error`, `limit_exceeded` |
| PDF characters in one page | 200000 | The page is skipped, with the reason `page_text_limit`. |
| PDF characters in one document | 20 million | `parse_error`, `limit_exceeded` |
| DOCX zip entries | 10000 | `parse_error`, `limit_exceeded` |
| DOCX uncompressed size | 256 MiB in total, 64 MiB for `word/document.xml` | `parse_error`, `limit_exceeded` |
| DOCX compression ratio | 100 for each large entry | `parse_error`, `limit_exceeded` |
| DOCX grid span | 64 | `parse_error`, `limit_exceeded` |
| HTML `<` characters | 500000 | `parse_error`, `limit_exceeded` |

If a parse runs longer than twice the deadline plus 300 seconds, the service becomes degraded. Uploads then get 503 until a restart.

## Python library

The library runs in your process, with no HTTP service. It does not change the process, and it turns tracing off only for its own calls.

```python
from xat_rag import (ChunkSource, ContextRequest, DocumentResult, RetrievalResult,
                     RetrievedChunk, SearchHit, Settings, XatRag)
from xat_rag_core.model import RawDocument, SourceKind
```

### `Settings`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `data_folder` | `Path` | Required | One SQLite file for each RAG, and the model check cache. |
| `recipes_folder` | `Path` | Required | The folder with `rags/` and `contexts/`. |
| `models_folder` | `Path` | Required | The model files. |
| `lock_file` | `Path` | Required | The model lock file. |
| `threads` | `int` | `0` | The ONNX Runtime threads. |
| `parse_deadline_seconds` | `float` | `60.0` | The parse deadline for each document. |

### `XatRag`

| Method | Returns | Purpose |
|---|---|---|
| `XatRag.open(settings)` | `XatRag` | Read the recipes, check the models and open the indexes. |
| `close()` | `None` | Close all indexes. |
| `rag_names()` | `list[str]` | The names of the RAGs. |
| `context_names()` | `list[str]` | The names of the contexts. |
| `needs_rebuild()` | `dict[str, str]` | The RAGs that need a rebuild, with the reason. |
| `ingest(documents)` | `list[DocumentResult]` | Ingest a sequence of `RawDocument`. One bad document does not stop the others. |
| `ingest_one(document)` | `DocumentResult` | Ingest one document. |
| `checkpoint(rag)` | `None` | Write the WAL file of a RAG into its index. Call it after a batch. |
| `delete(rag, doc_id)` | `bool` | Delete a document. `False` means that it was not found. |
| `rebuild(rag)` | `None` | Delete the index and make an empty one. |
| `retrieve(request)` | `RetrievalResult` | Return the ranked chunks of a `ContextRequest`. |

Failures raise a subclass of `XatRagError` from `xat_rag_core.errors`. The `error_type` of each subclass matches the [error types](#errors). A bad recipe or model raises `ConfigError`.

### Input types

`RawDocument(doc_id, rag, source_kind, data, uri="", title="", version="", media_type="", metadata={})`. The `data` field holds the bytes. `source_kind` is a `SourceKind`: `MARKDOWN`, `TEXT`, `DIFF`, `JIRA`, `PROMPT`, `PDF`, `DOCX` or `HTML`.

`ContextRequest(context, question, chunks=None, debug=False)`. With `chunks=None`, the value of the recipe applies.

### Result types

All result types are frozen dataclasses.

| Type | Fields |
|---|---|
| `RetrievalResult` | `chunks: tuple[RetrievedChunk, ...]`, `omitted: tuple[OmittedItem, ...]`, `debug: tuple[DebugCandidate, ...] \| None` |
| `RetrievedChunk` | `chunk_id`, `parent_id`, `kind`, `score`, `size_tokens`, `header`, `text`, `source: ChunkSource`, `metadata` |
| `ChunkSource` | `rag`, `doc_id`, `source_kind`, `uri`, `title`, `version`, `span` |
| `DocumentResult` | `doc_id`, `status`, `chunks`, `embedded`, `error_type`, `reason`, `message`, `pages_total`, `pages_indexed`, `skipped_pages`, `notes` |
| `SearchHit` | `chunk: RetrievedChunk`, `scores` |
| `OmittedItem` | `chunk_id`, `reason` |
| `DebugCandidate` | `chunk_id`, `rag`, `lexical_rank`, `dense_rank`, `rrf_score`, `rerank_score`, `rerank_truncated` |
