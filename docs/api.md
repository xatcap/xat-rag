# API reference

This page describes the HTTP API of the xat-rag service, version 0.1. The service code is in `packages/xat-rag-service/src/xat_rag_service/app.py`. See the [architecture](architecture.md), section 9, for the reasons.

The API returns retrieved text only. It calls no LLM, it builds no prompt and it adds no tags or escaping to the text. The app does these tasks. See the [app guide](app-guide.md).

The examples use one shell variable. `URL` is the `url` in the ready file.

```bash
URL=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["url"])' ready.json)
```

## Access and the request checks

The service has no authentication. Each program on the machine can connect to the TCP port, including the programs of other users. A program that connects can use every route. See [ADR 0015](adr/0015-remove-service-access-key.md).

A Unix socket has the mode `0600` only when `XatRag__ReadyFile` is set. Without a ready file, the socket is open to all users. With a ready file, it is open to all users for a short time at the start.

Every request goes through these checks, in this order:

1. The `Host` header must be `127.0.0.1`, `localhost` or `[::1]`, with or without a port. The check ignores case. A Unix socket client must also send a loopback name, for example `localhost`. Otherwise the service returns 400 `invalid_request_error`.
2. A request with an `Origin` header returns 400 `invalid_request_error`.

The service ignores an `Authorization` header. A client that still sends one is not refused for it.

The service sends no CORS headers.

FastAPI serves the OpenAPI schema at `/openapi.json`. The schema has the same checks as each other route. The docs pages of FastAPI are off.

## Errors

Each error has this body:

```json
{"error": {"message": "The document does not exist.", "type": "not_found_error"}}
```

| Type | Status | When |
|---|---|---|
| `invalid_request_error` | 400 | A body, a field, an upload or a query parameter is not valid. A question has no text after sanitizing. A rebuild runs while a job of the RAG runs. |
| `invalid_request_error` | 405 | A route is used with the wrong method. |
| `invalid_request_error` | 413 | A body is larger than its limit. |
| `not_found_error` | 404 | An unknown route, RAG, document, job or context. |
| `index_mismatch_error` | 409 | The index of the RAG does not match the models or the recipe. Rebuild the RAG. |
| `service_unavailable_error` | 503 | The service is degraded, with no `Retry-After`. Restart the service. Or the RAG is being rebuilt or stopped, with `Retry-After: 1`. |
| `overloaded_error` | 503 | The request pool is full (`Retry-After: 1`). Or the upload queue is full (`Retry-After: 5`). |
| `stage_timeout_error` | 504 | A stage took longer than its limit. The message names the stage. |
| `internal_error` | 500 | An unexpected failure, or a failed stage such as the reranker. The body has no details. |

A bad document in an upload does not give an HTTP error. It gives a `failed` result in the job. See [Jobs](#jobs).

Validation messages name the fields that failed. They never show the input value, and they never show an unknown key.

## Routes

### Health

`GET /health/live` returns 200 and `{"status": "live"}`.

`GET /health/ready` returns 200 when the service is ready:

```json
{"status": "ready", "version": "0.1.0", "needs_rebuild": []}
```

`needs_rebuild` lists the RAGs whose index does not match. Each of these RAGs gives 409 on its own routes. The other RAGs still work.

When a document ran too long, the service is degraded. The route returns 503:

```json
{"status": "degraded", "reason": "parse_timeout"}
```

### RAGs

`GET /v1/rags` lists the RAGs and the contexts:

```json
{"rags": [
   {"name": "docs", "needs_rebuild": false, "documents": 3, "chunks": 21},
   {"name": "jira", "needs_rebuild": true}
 ],
 "contexts": ["ask-docs", "triage"]}
```

`GET /v1/rags/{rag}` returns the counts and part of the manifest:

```json
{"name": "docs", "documents": 3, "chunks": 21,
 "manifest": {"index_format": 1, "embedder": "bge-small-en-v1.5", "embedder_sha256": "...",
              "dims": 384, "lexical_tokenizer": "code-v1",
              "size_tokenizer_id": "ministral-3-3b-tokenizer@99cf274236c6",
              "index_hash": "...", "chunkers": {"markdown": "markdown-v2+lcts1.1.3"}}}
```

`POST /v1/rags/{rag}/rebuild` deletes the index of the RAG and makes an empty one. It returns `{"rebuilt": true, "documents": 0}`. The app then sends all the documents again with `PUT`. The route returns 400 while a job of the RAG is queued or running.

### Documents

`PUT /v1/rags/{rag}/documents` starts an ingestion job. It returns 202:

```json
{"job_id": "j_0123456789abcdef0123"}
```

The body is `multipart/form-data`. See [Upload format](#upload-format).

`DELETE /v1/rags/{rag}/documents/{doc_id}` deletes one document and its chunks. Percent-encode the `doc_id`. It returns `{"deleted": true}`. It returns 404 when the document is not in the RAG. The service checkpoints the WAL file after the delete.

### Jobs

`GET /v1/jobs/{job_id}` returns the job:

```json
{"job_id": "j_0123456789abcdef0123", "rag": "docs", "status": "done", "total": 1, "done": 1,
 "error_type": null, "notes": [],
 "results": [
   {"doc_id": "docs:orders.md", "status": "created", "chunks": 4, "embedded": 4,
    "error_type": null, "reason": null, "message": null,
    "pages_total": null, "pages_indexed": null, "skipped_pages": [], "notes": []}
 ]}
```

`status` is one of `queued`, `running`, `done`, `cancelled` or `failed`. `done` counts the results that exist. `error_type` is set when the job fails. `notes` can hold `checkpoint_failed:<error type>`.

A document result has `status` `created`, `updated`, `unchanged` or `failed`. A failed result has an `error_type` (for example `parse_error`), a `reason` and a `message`. The reasons of `parse_error` are these:

- `invalid`: the bytes are not the kind of document, or the document has no text.
- `encrypted`: an encrypted PDF.
- `no_text_layer`: a PDF without any page that has text.
- `unsupported_media_type`: a DOCX file with macros.
- `too_large`: a text document above 4 MiB, an HTML document above 8 MiB, or a PDF or DOCX document above 32 MiB.
- `limit_exceeded`: a PDF, DOCX or HTML limit was passed. The message names the limit.
- `parse_timeout`: the parse deadline passed.

A PDF result also has `pages_total`, `pages_indexed` and `skipped_pages`. Each skipped page has `page`, `label` and `reason`. The reasons are `no_text`, `extract_error` and `page_text_limit`.

`notes` can hold these values: `binary_file_skipped` and `file_without_hunks_skipped` for diffs, `comments_skipped`, `headers_footers_skipped` and `tracked_insertions_kept` for DOCX.

`DELETE /v1/jobs/{job_id}` asks the job to stop before its next document. It returns the job at once. A job that is done or failed stays as it is.

Jobs live in memory. A restart of the service loses them. The service keeps the last 200 finished jobs. An unknown job ID returns 404.

### Search

`POST /v1/rags/{rag}/search` returns the candidates of one RAG. It is for debugging and evaluation. The app uses `POST /v1/context` for its questions.

Request:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `question` | string, at most 20000 characters | `""` | The question. |

```json
{"question": "Which port does the orders service use?"}
```

The response has the query and the hits:

```json
{"query": {"lexical_terms": ["port", "orders", "service"], "dense_text": "...", "rerank_text": "..."},
 "hits": [{"chunk_id": "docs:orders.md#h/orders-service/ports-and-health",
           "parent_id": null, "kind": "section", "score": 4.12, "size_tokens": 37,
           "header": "Orders service > Ports and health", "text": "...",
           "source": {"rag": "docs", "doc_id": "docs:orders.md", "source_kind": "markdown",
                      "uri": "orders.md", "title": "Orders service", "version": "4f2a9c1",
                      "span": {"type": "line", "line_start": 4, "line_end": 9}},
           "metadata": {"heading_level": 2},
           "lexical_rank": 1, "dense_rank": 2, "rrf_score": 0.0325, "rerank_score": 4.12}]}
```

Each hit has the fields of a chunk (see [Chunk fields](#chunk-fields)). It also has these stage fields:

- `lexical_rank` and `dense_rank`: the 1-based rank in each search, or `null` when the stage did not find the chunk.
- `rrf_score`: the score of the fusion.
- `rerank_score`: the score of the reranker, or `null` when the RAG has no reranker.

A search returns at most the `keep` value of the RAG hits. The hits are in the order of the RAG search, not in the order of a context result.

### Context

`POST /v1/context` returns the ranked chunks for a question. This is the main route. It is a retrieval call only. It calls no LLM.

Request:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `context` | string, 1 to 64 characters | (required) | The name of a context recipe. |
| `question` | string, at most 20000 characters | (required) | The question. Control and invisible characters are removed first. The question must still have text. |
| `chunks` | integer, 1 to 100 | The `chunks` value of the recipe, default 10 | The number of chunks to return, at most. It must be a JSON integer. A string or a number with a fraction gives 400. |
| `debug` | boolean | `false` | Adds the scores of each candidate to the response. |

Example:

```bash
curl -s -X POST "$URL/v1/context" \
  -H "Content-Type: application/json" \
  -d '{"context": "ask-docs", "question": "Which port does the orders service use?", "chunks": 5}'
```

The response:

```json
{"chunks": [{
   "chunk_id": "docs:orders.md#h/orders-service/database-timeouts",
   "parent_id": null,
   "kind": "section",
   "score": 6.59,
   "size_tokens": 37,
   "header": "Orders service > Database timeouts",
   "text": "A PSQLException with ...",
   "source": {
     "rag": "docs",
     "doc_id": "docs:orders.md",
     "source_kind": "markdown",
     "uri": "orders.md",
     "title": "Orders service",
     "version": "4f2a9c1",
     "span": {"type": "line", "line_start": 13, "line_end": 13}
   },
   "metadata": {"heading_level": 2}
 }],
 "omitted": [{"chunk_id": "docs:orders.md#h/orders-service/ports-and-health", "reason": "limit"}],
 "debug": null}
```

The rules of the result are these:

- `chunks` is in rank order, best first. The `score` of a chunk is never higher than the score of the chunk before it.
- `chunks` is an upper bound. Each RAG of the context gives at most its `keep` chunks, and the default `keep` is 8. A request for 10 chunks against one RAG with the default `keep` returns at most 8 chunks.
- `omitted` lists the candidates that were found but not returned. The `reason` is `duplicate` or `limit`. The `duplicate` reason means that the chunk repeats the content of a better chunk, or that its chunk ID is already in the result. The `limit` reason means that the result is full. A candidate that the `keep` value of its RAG removes is not listed.
- A chunk whose document is deleted while the request runs is left out. It is not listed in `omitted`.
- `debug` is `null` unless `debug` is `true`. When it is set, `debug` is a list. Each item has `chunk_id`, `rag`, `lexical_rank`, `dense_rank`, `rrf_score`, `rerank_score` and `rerank_truncated`.
- A context that uses a RAG with a `needs_rebuild` status returns 409.

#### Chunk fields

Each item of `chunks` and each hit of a search has these fields:

| Field | Type | Meaning |
|---|---|---|
| `chunk_id` | string | The stable ID of the chunk. Use it to find the chunk again. |
| `parent_id` | string or `null` | The ID of the parent section. A JIRA comment has the `desc` chunk of its issue as its parent. Otherwise it is `null`. |
| `kind` | string | `section`, `hunk`, `description`, `comment`, `prompt` or `page`. |
| `score` | number | The rerank score when the RAG has a reranker. Otherwise the RRF score. A higher score is a better match. |
| `size_tokens` | integer | The size of `header` and `text` with the size ruler of xat-rag. This is not a count of your LLM tokens. See [architecture](architecture.md#4-data-model). |
| `header` | string | The heading path or the title of the chunk. Untrusted text. |
| `text` | string | The stored text of the chunk. It has no tags and no escaping. Untrusted text. |
| `source` | object | Where the chunk came from. See the next table. |
| `metadata` | object | The metadata of the chunk. It has the fields that the parser wrote and the fields that the caller sent. Values are strings, integers, numbers or booleans. |

The `source` object has these fields:

| Field | Type | Meaning |
|---|---|---|
| `rag` | string | The name of the RAG. |
| `doc_id` | string | The document ID that the caller sent. |
| `source_kind` | string | The kind of the document: `markdown`, `text`, `diff`, `jira`, `prompt`, `pdf`, `docx` or `html`. |
| `uri` | string | The link that the caller sent. It is empty when the caller sent none. A PDF chunk ends with `#page=N`. |
| `title` | string | The title of the document. |
| `version` | string | The version that the caller sent for the document, for example a commit SHA. It is empty when the caller sent none. |
| `span` | object | The part of the document that holds the chunk. See the next section. |

The `metadata` object holds the parser fields. For example, the JIRA parser writes `jira_key`, `status`, `priority`, `labels` and `created`. The diff parser writes `path`, `path_name` and `change`. The markdown parser writes `heading_level`. The metadata also holds the fields that the caller sent in `metadata` of the upload. xat-rag does not filter on metadata in v0.1.

#### Span

The `span` has one of three forms:

- A line span has `type` `line`, `line_start` and `line_end`.
- A page span has `type` `page`, `page_start`, `page_end`, `label_start` and `label_end`.
- A block span has `type` `block`, `block_start` and `block_end`.

A PDF chunk has a `uri` that ends with `#page=N`. The page span gives the pages, and the `uri` gives the page to open first.

### Query build

`POST /v1/query/build` shows the query that a question makes. It takes the same body as the search route. It returns the query only:

```json
{"lexical_terms": ["NullPointerException", "OrderService.java"], "dense_text": "...",
 "rerank_text": "..."}
```

`lexical_terms` has at most 32 terms. `rerank_text` has at most the first 600 characters of the question. `dense_text` has the whole question, after sanitizing. The embedder keeps only the part that fits its input.

## Upload format

`PUT /v1/rags/{rag}/documents` takes `multipart/form-data`. The request needs a `boundary` parameter. The body has these parts:

- One part named `documents`. Its content is a JSON list with one object for each document.
- One part for each document file. Each part has a `name` in its `Content-Disposition` header. The names are unique in the request.

Each part needs a name, and it must not have a `Content-Transfer-Encoding` header. xat-rag reads no other part header.

Each object in the `documents` list has these fields:

| Field | Type | Required | Meaning |
|---|---|---|---|
| `doc_id` | string, 1 to 512 characters | Yes | The ID of the document. Use `<rag>:<name>`, for example `docs:orders.md`. |
| `kind` | string | Yes | A source kind of the RAG: `markdown`, `text`, `diff`, `jira`, `prompt`, `pdf`, `docx` or `html`. |
| `part` | string, 1 to 128 characters | Yes | The name of the file part that holds the bytes. |
| `uri` | string, at most 2048 characters | No | A link to the source, for example a ticket URL. It goes into the result. |
| `title` | string, at most 1024 characters | No | The title. A parser can use the title of the document when this is empty. |
| `version` | string, at most 256 characters | No | A version, for example a commit SHA or the JIRA `updated` value. |
| `metadata` | object of strings, integers, numbers and booleans | No | Metadata for the chunks of the document. It goes into the `metadata` of each chunk result. |

Unknown fields are errors. A `kind` that the RAG has no source for is an error.

Example with `curl`:

```bash
curl -s -X PUT "$URL/v1/rags/docs/documents" \
  -F 'documents=[{"doc_id": "docs:orders.md", "kind": "markdown", "part": "f1", "uri": "orders.md"}];type=application/json' \
  -F 'f1=@orders.md'
```

The request limits are these:

- 64 MiB for the whole request. A larger request returns 413.
- 256 objects in the `documents` list for each request.
- 4 MiB for each text document (`markdown`, `text`, `diff`, `jira` and `prompt`).
- 8 MiB for each `html` document.
- 32 MiB for each `pdf` or `docx` document.

The bytes never go to temporary files. The service keeps them in memory until the job takes them.

## Settings

The service reads its settings from environment variables. The names are not case-sensitive.

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `XatRag__Urls` | No | `http://127.0.0.1:0` | `http://127.0.0.1:<port>` or `http://[::1]:<port>`. Port 0 lets the system choose. Or `http://unix:<path>` for a Unix socket. |
| `XatRag__DataFolder` | Yes | (none) | The folder for the indexes, the lock file `xat-rag.lock` and the model check cache. The mode is `0700`. |
| `XatRag__RecipesFolder` | Yes | (none) | The folder with `rags/*.toml` and `contexts/*.toml`. |
| `XatRag__ModelsFolder` | Yes | (none) | The folder with the model files. |
| `XatRag__LockFile` | Yes | (none) | The model lock file, `build/models.lock.json`. |
| `XatRag__ReadyFile` | No | (none) | The file that holds `{url, pid, version}` when the service is ready. The mode is `0600`. |
| `XatRag__StopWhenInputCloses` | No | `false` | `true` stops the service when its standard input closes. |
| `XatRag__QueueLimit` | No | `8` | The number of requests that can wait or run in the pool. From 1 to 1000. |
| `XatRag__Threads` | No | `0` | The threads of ONNX Runtime. `0` lets ONNX Runtime choose. From 0 to 256. |

`XatRag__Urls` accepts loopback addresses only. A `localhost` host is listened on as `127.0.0.1`. Any other host is an error at start.

The service removes `LANGSMITH_*`, `LANGCHAIN_*` and each variable whose name ends in `_API_KEY`, `_TOKEN`, `_ENDPOINT` or `_ENDPOINTS`, except the `XatRag__*` variables. An unrelated variable such as `OTEL_EXPORTER_OTLP_ENDPOINT` is removed too.

## Lifecycle

1. The service checks its settings.
2. It takes the lock of the data folder. A second service with the same folder stops with an error.
3. It loads the recipes, the models and the indexes. A recipe with an unknown key stops the start, and the message names the field.
4. It starts to listen. It writes the ready file. The ready file is deleted when the service stops.

A ready file looks like this:

```json
{"url": "http://127.0.0.1:52341", "pid": 4312, "version": "0.1.0"}
```

For a Unix socket, `url` is `http://unix:<path>`.

The service stops on `SIGINT`, on `SIGTERM`, or when standard input closes with `XatRag__StopWhenInputCloses=true`. It stops the jobs, waits up to 30 seconds for a running document, and closes the indexes.

## Limits

| Limit | Value | Result when passed |
|---|---|---|
| JSON request body (context, search and query build) | 1 MiB | 413 |
| Upload request | 64 MiB | 413 |
| Documents in one upload | 256 | 400 |
| Queued upload data in the service | 256 MiB | 503 `overloaded_error`, `Retry-After: 5` |
| Requests in the pool | `XatRag__QueueLimit` (default 8) | 503 `overloaded_error`, `Retry-After: 1` |
| `question` | 20000 characters | 400 |
| `chunks` | 1 to 100 | 400 |
| Stage times | Query build 200 ms, retrieve 500 ms, embed 1000 ms, rerank 2500 ms, pack 300 ms, in each recipe. The `pack` stage is the merge of the RAG results. | 504 `stage_timeout_error` |

The limits of the parsers are in the [architecture](architecture.md#5-ingestion-flow), section 5.

## Examples

Ingest a document and wait for the job:

```bash
JOB=$(curl -s -X PUT "$URL/v1/rags/docs/documents" \
  -F 'documents=[{"doc_id": "docs:orders.md", "kind": "markdown", "part": "f1"}];type=application/json' \
  -F 'f1=@orders.md' | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])')
curl -s "$URL/v1/jobs/$JOB"
```

Ask for the ranked chunks of a question:

```bash
curl -s -X POST "$URL/v1/context" -H "Content-Type: application/json" \
  -d '{"context": "ask-docs", "question": "Which port does the orders service use?", "chunks": 5}'
```

## Removed in this version

These routes, fields, settings and error types are removed. A request that sends a removed field gets 400. A removed route gets 404. See the [changelog](../CHANGELOG.md).

- Routes: `POST /v1/tokens/count`, `GET /v1/prompts/{name}/{version}` and `POST /v1/citations/validate`.
- Fields of `POST /v1/context`: `log_lines`, `selected_lines`, `reserved_output` and `input_max_tokens`.
- Fields of `POST /v1/rags/{rag}/search` and `POST /v1/query/build`: `builder` and `log_lines`.
- Fields of the response of `POST /v1/context`: `pack_id`, `generator`, `max_tokens`, `budget`, `pinned`, `input`, `rendered` and `citation`. The chunk fields `label`, `tokens`, `doc_id`, `rag`, `uri`, `title` and `span` moved into `source` or were renamed.
- Omitted reasons `budget` and `quota`.
- Debug field `exact_rank`, and the query field `exact_lookups`.
- Error type `context_too_large_error` (422).
- Error type `authentication_error` (401). See [ADR 0015](adr/0015-remove-service-access-key.md).
- Setting `XatRag__PromptsFolder`.
- Setting `XatRag__AccessKey`. The service ignores it.
