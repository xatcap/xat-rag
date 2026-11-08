# xat-rag

The xat-rag library: recipes, the composition root, ingestion and the retrieve use case. See the [architecture](../../docs/architecture.md).

The [root README](../../README.md#library) has a quick start. The [app guide](../../docs/app-guide.md) describes the work that the app does with the results.

The `XatRag` facade has these methods:

| Method | What it does |
|---|---|
| `XatRag.open(settings)` | Reads the recipes, checks and loads the models, and opens the indexes. |
| `ingest(documents)`, `ingest_one(document)` | Parse, chunk, embed and store documents. Each document has its own result. |
| `delete(rag, doc_id)` | Deletes a document, and then checkpoints the WAL file. |
| `checkpoint(rag)` | Writes the WAL file of a RAG into its file. Call it after a batch of ingestion. |
| `rebuild(rag)` | Deletes the index of a RAG and makes an empty one. The app must send the documents again. The service refuses a rebuild while a job of the RAG runs. |
| `needs_rebuild()` | Returns the RAGs whose index does not match, with the reason. |
| `search(rag, query)` | Returns the candidates of one RAG, with the scores of each stage. For debugging and evaluation. |
| `search_hits(rag, query)` | Returns the same candidates as public chunks, with the scores of each stage. |
| `retrieve(ContextRequest)` | Returns the ranked chunks of a context. Each chunk has its text, its header, its source and its metadata. It returns the omitted candidates too. |
| `context_recipe(context)` | Returns the recipe of a context. |

The public API uses xat-rag types only. The types are `RetrievalResult`, `RetrievedChunk`, `ChunkSource`, `SearchHit`, `OmittedItem` and `DocumentResult`. The planned `xat_rag.langchain` module is not in v0.1.

The library calls no LLM. It builds no prompt and it escapes no text. Library mode does not change the process. It does not call `harden_process`, and it turns tracing off only for its own calls (ADR 0011).
