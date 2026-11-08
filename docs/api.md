# API reference

This page describes the HTTP API of the xat-rag service, version 0.1. The service code is in `packages/xat-rag-service/src/xat_rag_service/app.py`. See the [architecture](architecture.md), section 9, for the reasons.

The examples use two shell variables. `URL` is the `url` in the ready file. `KEY` is the access key.

```bash
URL=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["url"])' ready.json)
KEY=...   # The access key that the app set in XatRag__AccessKey.
```

## Access and the request checks

Every request goes through these checks, in this order:

1. The `Host` header must be `127.0.0.1`, `localhost` or `[::1]`, with or without a port. The check ignores case. A Unix socket client must also send a loopback name, for example `localhost`. Otherwise the service returns 400 `invalid_request_error`.
2. A request with an `Origin` header returns 400 `invalid_request_error`.
3. Each route, except `GET /health/live`, needs `Authorization: Bearer <key>`. A wrong or missing key returns 401 `authentication_error`.

The service sends no CORS headers.

FastAPI serves the OpenAPI schema at `/openapi.json`. The schema needs the access key. The docs pages of FastAPI are off.

## Errors

Each error has this body:

```json
{"error": {"message": "The document does not exist.", "type": "not_found_error"}}
```

| Type | Status | When |
|---|---|---|
| `invalid_request_error` | 400 | A body, a field, an upload or a query parameter is not valid. A rebuild runs while a job of the RAG runs. A context request sets a value above its recipe. |
| `invalid_request_error` | 405 | A route is used with the wrong method. |
| `invalid_request_error` | 413 | A body is larger than its limit. |
| `authentication_error` | 401 | The access key is missing or wrong. |
| `not_found_error` | 404 | An unknown route, RAG, document, job, context or prompt. |
| `index_mismatch_error` | 409 | The index of the RAG does not match the models or the recipe. Rebuild the RAG. |
| `context_too_large_error` | 422 | The pinned prompts and the input do not fit in the window. |
| `service_unavailable_error` | 503 | The service is degraded, with no `Retry-After`. Restart the service. Or the RAG is being rebuilt or stopped, with `Retry-After: 1`. |
| `overloaded_error` | 503 | The request pool is full (`Retry-After: 1`). Or the upload queue is full (`Retry-After: 5`). |
| `stage_timeout_error` | 504 | A stage took longer than its limit. The message names the stage. |
| `internal_error` | 500 | An unexpected failure. The body has no details. |

A bad document in an upload does not give an HTTP error. It gives a `failed` result in the job. See [Jobs](#jobs).

Validation messages name the fields that failed. They never show the input value, and they never show an unknown key.

## Routes

### Health

`GET /health/live` returns 200 and `{"status": "live"}`. It needs no access key.

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
 "contexts": ["ask-docs", "log-triage"]}
```

`GET /v1/rags/{rag}` returns the counts and part of the manifest:

```json
{"name": "docs", "documents": 3, "chunks": 21,
 "manifest": {"index_format": 1, "embedder": "bge-small-en-v1.5", "embedder_sha256": "...",
              "dims": 384, "lexical_tokenizer": "code-v1",
              "generator_tokenizer_id": "ministral-3-3b-tokenizer@99cf274236c6",
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

`POST /v1/rags/{rag}/search` returns the ranked candidates of one RAG. It is for debugging and evaluation.

Request:

```json
{"builder": "text-v1", "question": "Which port does the orders service use?"}
```

`builder` is `text-v1` (the default) or `log-v1`. `log_lines` takes the log lines for `log-v1`. The response has the query and the hits:

```json
{"query": {"lexical_terms": ["port", "orders", "service"], "dense_text": "...", "rerank_text": "...",
           "exact_lookups": []},
 "hits": [{"chunk_id": "docs:orders.md#h/orders-service/ports-and-health", "uri": "orders.md",
           "span": {"type": "line", "line_start": 4, "line_end": 9},
           "lexical_rank": 1, "dense_rank": 2, "exact_rank": null,
           "rrf_score": 0.0325, "rerank_score": 4.12}]}
```

Each rank is 1-based, or `null` when the stage did not find the chunk. `rerank_score` is `null` when the RAG has no reranker.

### Context

`POST /v1/context` makes a context pack. This is the main route.

Request:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `context` | string, 1 to 64 characters | (required) | The name of a context recipe. |
| `question` | string, at most 20000 characters | `""` | The question. Required for `text-v1`. |
| `log_lines` | list of strings, at most 2000 | `[]` | The log lines. Each line is cut to 4096 characters. Only for `log-v1`. |
| `selected_lines` | list of integers, at most 2000 | `[]` | Zero-based indexes of `log_lines` that the user selected. |
| `reserved_output` | integer or `null` | The recipe value | Lowers the tokens for the answer. It cannot go above the recipe value. |
| `input_max_tokens` | integer or `null` | The recipe value | Lowers the cap of the input section. It cannot go above the recipe value. |
| `debug` | boolean | `false` | Adds the scores of each candidate to the response. |

Example:

```bash
curl -s -X POST "$URL/v1/context" -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"context": "ask-docs", "question": "Which port does the orders service use?"}'
```

The response is the context pack:

```json
{"pack_id": "p_3f9c2a7d1e4b8c60a1f2", "generator": "ministral-3-3b", "max_tokens": 768,
 "budget": {"window": 8192, "reserved_output": 768, "overhead": 240, "pinned": 310, "input": 640,
            "chunks": 4120, "margin": 246, "unused": 1868, "floors_lowered": false},
 "pinned": [{"id": "prompt:log-triage-system@3", "tokens": 310, "text": "..."}],
 "chunks": [{"label": "C1", "chunk_id": "docs:orders.md#h/orders-service/ports-and-health",
             "doc_id": "docs:orders.md", "rag": "docs", "kind": "section", "uri": "orders.md",
             "title": "...", "score": 0.91, "span": {"type": "line", "line_start": 4, "line_end": 9},
             "tokens": 220, "text": "..."}],
 "input": {"text": "Question: ...", "tokens": 40, "dropped_templates": 0, "truncated": false},
 "rendered": {"messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]},
 "citation": {"grammar": "root ::= ...", "json_schema": {"type": "object"}},
 "omitted": [{"chunk_id": "...", "reason": "budget"}],
 "debug": null}
```

Send `rendered.messages`, `citation.grammar` and `max_tokens` to the engine. Do not send `citation.json_schema` with the grammar.

The `span` has one of three forms. A line span has `type` `line`, `line_start` and `line_end`. A page span has `type` `page`, `page_start`, `page_end`, `label_start` and `label_end`. A block span has `type` `block`, `block_start` and `block_end`.

A PDF chunk has a `uri` that ends with `#page=N`.

When `debug` is `true`, `debug` is a list. Each item has `chunk_id`, `rag`, `lexical_rank`, `dense_rank`, `exact_rank`, `rrf_score`, `rerank_score` and `rerank_truncated`.

A context that uses a RAG with a `needs_rebuild` status returns 409. The `reason` of each omitted chunk is `budget`, `quota` or `duplicate`.

### Query builder

`POST /v1/query/build` shows the query that a builder makes from the input. It takes the same body as the search route. It returns the query only:

```json
{"lexical_terms": ["NullPointerException", "OrderService.java"], "dense_text": "...",
 "rerank_text": "...", "exact_lookups": [{"field": "path_name", "value": "orderservice.java"}]}
```

### Tokens

`POST /v1/tokens/count` counts the tokens of a text with a generator tokenizer:

```json
{"text": "Hello world.", "generator": "ministral-3-3b"}
```

The response is `{"tokens": 3, "generator": "ministral-3-3b"}`. The `generator` field is optional. Its default is `ministral-3-3b`. An unknown generator returns 400.

### Prompts

`GET /v1/prompts/{name}/{version}` returns a pinned prompt. The `version` is an integer:

```json
{"id": "prompt:log-triage-system@1", "name": "log-triage-system", "version": 1, "text": "..."}
```

An unknown prompt returns 404. The API cannot add or change prompts.

### Citations

`POST /v1/citations/validate` checks an answer against the chunk list of its pack:

```json
{"answer": "{\"answer\": \"The port is 8080.\", \"citations\": [\"C1\"]}",
 "chunks": [{"label": "C1", "chunk_id": "docs:orders.md#h/orders-service/ports-and-health"}]}
```

The response:

```json
{"valid": true, "problem": null, "answer": "{\"answer\": \"The port is 8080.\", \"citations\": [\"C1\"]}",
 "citations": [{"label": "C1", "chunk_id": "docs:orders.md#h/orders-service/ports-and-health",
                "uri": "orders.md"}],
 "unknown_labels": [], "missing_chunks": []}
```

`problem` is `null` when the answer is valid. Otherwise it is one of these values:

- `not_json`: the answer is not valid JSON. An answer that the engine cut at `max_tokens` has this problem.
- `bad_shape`: the JSON does not have a string `answer` and a list `citations` of strings.
- `unknown_label`: a citation label is not in the `chunks` list. The list is in `unknown_labels`.
- `missing_chunk`: a cited chunk no longer exists in any RAG. The IDs are in `missing_chunks`.

The `uri` of a citation is empty when the chunk does not exist.

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
| `metadata` | object of strings, integers, numbers and booleans | No | Metadata for exact lookups and filters. A JIRA parser sets `jira_key`, and a diff parser sets `path_name`. |

Unknown fields are errors. A `kind` that the RAG has no source for is an error.

Example with `curl`:

```bash
curl -s -X PUT "$URL/v1/rags/docs/documents" -H "Authorization: Bearer $KEY" \
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
| `XatRag__AccessKey` | Yes | (none) | The access key. At least 16 ASCII characters. The service reads it once and removes it from the environment. |
| `XatRag__DataFolder` | Yes | (none) | The folder for the indexes, the lock file `xat-rag.lock` and the model check cache. The mode is `0700`. |
| `XatRag__RecipesFolder` | Yes | (none) | The folder with `rags/*.toml` and `contexts/*.toml`. |
| `XatRag__ModelsFolder` | Yes | (none) | The folder with the model files. |
| `XatRag__LockFile` | Yes | (none) | The model lock file, `build/models.lock.json`. |
| `XatRag__PromptsFolder` | No | (none) | The folder with the pinned prompts. |
| `XatRag__ReadyFile` | No | (none) | The file that holds `{url, pid, version}` when the service is ready. The mode is `0600`. |
| `XatRag__StopWhenInputCloses` | No | `false` | `true` stops the service when its standard input closes. |
| `XatRag__QueueLimit` | No | `8` | The number of requests that can wait or run in the pool. From 1 to 1000. |
| `XatRag__Threads` | No | `0` | The threads of ONNX Runtime. `0` lets ONNX Runtime choose. From 0 to 256. |

`XatRag__Urls` accepts loopback addresses only. A `localhost` host is listened on as `127.0.0.1`. Any other host is an error at start.

The service removes `LANGSMITH_*`, `LANGCHAIN_*` and each variable whose name ends in `_API_KEY`, `_TOKEN`, `_ENDPOINT` or `_ENDPOINTS`, except the `XatRag__*` variables. An unrelated variable such as `OTEL_EXPORTER_OTLP_ENDPOINT` is removed too.

## Lifecycle

1. The service checks its settings and the access key.
2. It takes the lock of the data folder. A second service with the same folder stops with an error.
3. It loads the recipes, the prompts, the models and the indexes.
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
| JSON request body (context, search, query build, tokens and citations) | 1 MiB | 413 |
| Upload request | 64 MiB | 413 |
| Documents in one upload | 256 | 400 |
| Queued upload data in the service | 256 MiB | 503 `overloaded_error`, `Retry-After: 5` |
| Requests in the pool | `XatRag__QueueLimit` (default 8) | 503 `overloaded_error`, `Retry-After: 1` |
| `question` | 20000 characters | 400 |
| `log_lines` | 2000 lines, each cut to 4096 characters | 400 for the count, cut for the length |
| `answer` for citations | 1 MiB | 413 |
| `chunks` for citations | 10000 items | 400 |
| Stage times | Query build 200 ms, retrieve 500 ms, embed 1000 ms, rerank 2500 ms, pack 300 ms, in each recipe | 504 `stage_timeout_error` |

The limits of the parsers are in the [architecture](architecture.md#5-ingestion-flow), section 5.

## Examples

Ingest a document and wait for the job:

```bash
JOB=$(curl -s -X PUT "$URL/v1/rags/docs/documents" -H "Authorization: Bearer $KEY" \
  -F 'documents=[{"doc_id": "docs:orders.md", "kind": "markdown", "part": "f1"}];type=application/json' \
  -F 'f1=@orders.md' | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])')
curl -s "$URL/v1/jobs/$JOB" -H "Authorization: Bearer $KEY"
```

Ask for a context pack, and then check the answer of the model:

```bash
curl -s -X POST "$URL/v1/context" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"context": "log-triage", "log_lines": ["ERROR [orders] OrderNotFoundException: order 9f1c"], "selected_lines": [0]}'
curl -s -X POST "$URL/v1/citations/validate" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"answer": "{\"answer\": \"...\", \"citations\": [\"C1\"]}", "chunks": [{"label": "C1", "chunk_id": "docs:orders.md#h/orders-service/order-lookup-errors"}]}'
```
