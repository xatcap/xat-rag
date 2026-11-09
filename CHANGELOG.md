# Changelog

## Unreleased

### Breaking changes

xat-rag is now retrieval-only. It returns ranked chunks of text, and it has no model knowledge. The app fits the chunks to its LLM, builds the prompt, escapes the text and checks the citations. See [ADR 0014](docs/adr/0014-retrieval-only-and-app-side-fitting.md) and [ADR 0013](docs/adr/0013-remove-log-handling-and-exact-lookups.md). The [app guide](docs/app-guide.md) describes the new work of the app.

Log handling and exact lookups are removed. There is one query path for a plain question.

The access key of the service is removed. See [ADR 0015](docs/adr/0015-remove-service-access-key.md). Each program on the machine can now read and change the indexes through the service. The error type `authentication_error` (401) is removed.

### Upgrade steps

Update the service first, and then remove the key from the app. An old service refuses a request without a key. The service ignores a stale `XatRag__AccessKey` variable and a stale `Authorization` header.

1. Edit each recipe. Remove the keys that the service refuses. The service refuses an old key at startup, and the message names the field. See the removed keys below.
2. Remove `XatRag__PromptsFolder` from the environment of the service.
3. Update each caller. Send `question` and, when needed, `chunks`. Read the `chunks` list of the response. Count the tokens of the chunks with the tokenizer of the LLM of the app.
4. Update the code that used `rendered.messages`, `citation.grammar` or `POST /v1/citations/validate`. The app now builds these parts itself.
5. No index rebuild is needed. The manifest key `generator_tokenizer_id` is read as `size_tokenizer_id`, and its value is the same.

### Removed routes

- `POST /v1/tokens/count`
- `GET /v1/prompts/{name}/{version}`
- `POST /v1/citations/validate`

A request to a removed route gets 404.

### Removed request fields

- `POST /v1/context`: `log_lines`, `selected_lines`, `reserved_output` and `input_max_tokens`.
- `POST /v1/rags/{rag}/search` and `POST /v1/query/build`: `builder` and `log_lines`.

A request with a removed field gets 400.

### Changed request fields

- `POST /v1/context`: `question` is required. It has at most 20000 characters.
- `POST /v1/context`: `chunks` is new. It is an integer from 1 to 100, and it sets the number of chunks to return, at most. The default is the `chunks` value of the context recipe, which is 10.

### Removed response fields

- `POST /v1/context`: `pack_id`, `generator`, `max_tokens`, `budget`, `pinned`, `input`, `rendered` and `citation`.
- Each chunk: `label` and `tokens` are removed. `tokens` is now `size_tokens`. `doc_id`, `rag`, `uri`, `title` and `span` moved into the new `source` object.
- Omitted reasons `budget` and `quota` are removed. The reason `limit` is new.
- The debug field `exact_rank`, and the query field `exact_lookups`.

### Added response fields

- Each chunk: `parent_id`, `header`, `source` (with `rag`, `doc_id`, `source_kind`, `uri`, `title`, `version` and `span`) and `metadata`.
- `POST /v1/rags/{rag}/search` hits have the chunk fields. They keep the stage scores.

### Removed error type

- `context_too_large_error` (422).

### Removed settings

- `XatRag__PromptsFolder`.

### Removed recipe keys

- `[rag]`: `generator`.
- `[retrieval]`: `exact_k`.
- `[context]`: `generator`, `window`, `reserved_output`, `input_max_tokens`, `pinned`, `order`, `citation` and `query_builder`.
- `[[context.sources]]`: `min_tokens` and `max_share`. Only `rag` remains.

### Added recipe keys

- `[context]`: `chunks`, from 1 to 100. The default is 10.

### Changed behavior

- The `[limits]` value `pack` limits the merge of the RAG results. It no longer limits a packing step.
- Startup refuses a context whose RAGs have no reranker and have different `rrf_k` values.
- A chunk whose document is deleted during a request is left out of the result.
- A chunk has a `score`, which is the rerank score when the RAG has a reranker, and otherwise the RRF score.
- The retrieval gate checks the final list of each case. It checks the recall at 5 and at 10, and the final MRR. The budget rule and the pack recall are removed. The source coverage is reported only.

### Library changes

- Removed methods of `XatRag`: `compose`, `prompt`, `check_citations`, `chunk_uris`, `count_tokens` and `generator_profile`.
- Added method: `XatRag.retrieve(ContextRequest)`. It returns a `RetrievalResult`.
- `ContextRequest` has the fields `context`, `question`, `chunks` and `debug`. The fields `log_lines`, `selected_lines`, `reserved_output` and `input_max_tokens` are removed.
- `ContextPack` is now `RetrievalResult`, and `PackedChunk` is now `RetrievedChunk`.
- `Settings.prompts_folder` is removed.

### Packages and tools

- The package `xat-rag-logs` is removed. It held `LogQueryBuilder`.
- In `xat-rag-core`, the modules `budget`, `packer`, `render` and `citations` are removed. The module `ranking` is added.
- In `xat-rag-tokenizers`, the module `profiles` is removed. The counter is the fixed size ruler.
- In `xat-rag-sqlite`, `ExactLookupRetriever` is removed.
- `tools/measure_profile.py` is removed, with its CI step. `mistral-common` is no longer a development dependency.
- The model lock role `generator_tokenizer` is now `size_tokenizer`. The file `tekken.json` is no longer in the lock.
- Index manifest: the key `generator_tokenizer_id` is now `size_tokenizer_id`. An old manifest still loads.

### Added documents

- [App guide](docs/app-guide.md): tokens, prompts, escaping, citations, the question and errors.
- [ADR 0013](docs/adr/0013-remove-log-handling-and-exact-lookups.md) and [ADR 0014](docs/adr/0014-retrieval-only-and-app-side-fitting.md).
