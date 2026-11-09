# Changelog

## Unreleased

### Breaking changes

xat-rag is now retrieval-only. It returns ranked chunks of text, and it has no model knowledge. The app fits the chunks to its LLM, builds the prompt, escapes the text and checks the citations. The [quickstart](docs/quickstart.md#6-what-your-app-must-do) describes the work of the app.

Log handling and exact lookups are removed. There is one query path for a plain question.

The access key of the service is removed. Each program on the machine can now read and change the indexes through the service. The error type `authentication_error` (401) is removed.

### Upgrade steps

Update the service first, and then remove the key from the app. An old service refuses a request without a key. The service ignores a stale `XatRag__AccessKey` variable and a stale `Authorization` header.

1. Edit each recipe. Remove the keys that the service refuses. The service refuses an old key at startup, and the message names the field. See the removed keys below.
2. Remove `XatRag__PromptsFolder` from the environment of the service.
3. Update each caller. Send `question` and, when needed, `chunks`. Read the `chunks` list of the response. Count the tokens of the chunks with the tokenizer of the LLM of the app.
4. Update the code that used `rendered.messages`, `citation.grammar` or `POST /v1/citations/validate`. The app now builds these parts itself.
5. The manifest key `generator_tokenizer_id` is read as `size_tokenizer_id`, and its value is the same. Thus, these steps need no index rebuild. The [parser changes](#document-parsers-and-chunk-splits) need a rebuild.

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
- Omitted reason `low_score`. An app with an exhaustive match on the omitted reasons must add this reason.

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
- `[context]`: `min_score`, a finite number. It has no default, so no score filter applies. A candidate with a lower score is omitted with the reason `low_score`. The RAGs of the context need a reranker.

### Changed behavior

- The `[limits]` value `pack` limits the merge of the RAG results. It no longer limits a packing step.
- Startup refuses a context whose RAGs have no reranker and have different `rrf_k` values.
- A chunk whose document is deleted during a request is left out of the result.
- A chunk has a `score`, which is the rerank score when the RAG has a reranker, and otherwise the RRF score.
- The retrieval gate checks the final list of each case. It checks the recall at 5 and at 10, and the final MRR. The budget rule and the pack recall are removed. The source coverage is reported only.
- The default contexts `ask-docs`, `ask-pdfs` and `triage` set `min_score = -5`. They can now return an empty list. Apps with their own recipes are unchanged, unless they add the `min_score` key.

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

### Release folder and default paths

- The frozen service has default paths. They are next to its executable: `data/`, `recipes/`, `models/`, `models/models.lock.json` and `data/ready.json`. A release folder starts with no path setting.
- A path setting with an empty value uses its default. An empty `XatRag__ReadyFile` means no ready file.
- From source, no default applies. The four path settings are required, as before.
- The release folder has empty `data/` and `models/` folders. The models are not in the release. Extract the models archive, `xat-rag-models-<tag>.zip`, into `models/`.
- Startup failures exit with code 2. They print one line that starts with `xat-rag:`.
- The stale ready file is deleted after the data folder lock is taken. A second start no longer deletes the ready file of the running service.
- The Unix socket of the frozen service has the mode `0600` by default.

### Upgrade steps for the release folder

1. Move `data/` and `models/` out of the release folder before you replace it. Replacing the folder deletes both folders.
2. Or send the documents again, after the new release starts, to build the indexes again.
3. If the release folder is read-only, set `XatRag__DataFolder` to a folder that the user can write to.

### Default recipes

- A release has the default RAG `pdfs`, which has the kind `pdf`. It has the default context `ask-pdfs`, which searches only `pdfs`.
- At start, the service creates the file `data/pdfs.sqlite`, as it does for each other RAG.
- A user with an own recipe named `pdfs` or `ask-pdfs` in the recipes folder must check for a name conflict.
- The default contexts `ask-docs`, `ask-pdfs` and `triage` set `min_score = -5`. See the changed behavior above.
- A release has the default RAGs `sheets`, `web` and `word`, with the contexts `ask-sheets`, `ask-web` and `ask-word`. These contexts also set `min_score = -5`.
- A release has the default RAGs `text` and `prompts`, with the contexts `ask-text` and `ask-prompts`. Each source kind has a default RAG.
- A release has the default contexts `ask-jira` and `ask-diffs`. `ask-jira` searches only `jira`, and `ask-diffs` searches only `diffs`.
- A release has the default RAG `all`, which accepts all nine source kinds, and the default context `ask-all`, which searches only `all`.
- The contexts `ask-text`, `ask-prompts`, `ask-jira`, `ask-diffs` and `ask-all` also set `min_score = -5`.
- At start, the service creates the files `data/text.sqlite`, `data/prompts.sqlite` and `data/all.sqlite`.
- A user with an own RAG recipe named `text`, `prompts` or `all`, or an own context recipe named `ask-text`, `ask-prompts`, `ask-jira`, `ask-diffs` or `ask-all`, must check for a name conflict.

### Document parsers and chunk splits

- The PDF, DOCX and HTML parsers now use Docling. For these kinds, the chunk `text` is Markdown.
- The source kind `xlsx` is new. An Excel chunk has a span of the type `cell`, with `sheet`, `row_start` and `row_end`.
- The HTML span changes from `line` to `block`. A caller that reads HTML line numbers must change.
- A PDF section now follows the headings of the document. The chunk kind `page` is removed.
- The chunker splits large Markdown, text, PDF, DOCX, HTML and Excel sections at their blocks. The embedder can choose the split points. A document with more than 20000 parts gets the note `semantic_split_unit_limit`.
- An upload that matches the stored upload gets the status `unchanged`, and it is not parsed.
- The parse deadline of a PDF is 2 seconds for each page, if that is longer than the normal deadline.
- A PDF that opens with an empty password is indexed.
- A RAG with a `pdf` source needs the PDF layout models. The models archive holds them.
- The reason `extract_error` of a skipped page is removed.
- The notes `comments_skipped`, `headers_footers_skipped` and `tracked_insertions_kept` are removed.
- The service no longer removes the `LANGSMITH_*` and `LANGCHAIN_*` variables.
- In `xat-rag-core`, the modules `lc_codec` and `tracing` are removed. The modules `markdown_form` and `spans` are added.

### Upgrade steps for the parsers

A RAG with a Markdown, text, PDF, DOCX or HTML source returns `409 index_mismatch_error` after the upgrade.

1. Rebuild each of these RAGs.
2. Upload the documents of each RAG again.

A RAG with only diff, JIRA or prompt sources needs no rebuild.
