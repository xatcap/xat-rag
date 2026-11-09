# xat-rag-core

The domain model, the ports and the rules of xat-rag. See the [architecture](../../docs/architecture.md).

| Module | What it does |
|---|---|
| `errors` | The error types and their HTTP status codes ([ADR 0009](../../docs/adr/0009-error-model.md)). |
| `model` | `SourceDocument`, `Chunk`, the span types, `Candidate`, `RawDocument` and the parse result types. |
| `ids` | Content hashes, chunk IDs, heading anchors and slugs. `log_id` makes a hashed ID for logs. No code uses it yet. |
| `parsing` | `SectionParser` (the protocol of the parsers), `Deadline`, `decode_text` and `SectionBlobParser`, a LangChain adapter. |
| `lc_codec` | The only code that reads or writes LangChain `Document.metadata`. |
| `query` | `RetrievalQuery` and `build_query()`. The query has the lexical terms, the dense text and the rerank text. |
| `sanitize` | Removes control, invisible and bidirectional characters, and normalizes to NFC. |
| `fusion` | Reciprocal Rank Fusion with `chunk_id` tie-breaks. |
| `mmr` | Duplicate removal and Maximal Marginal Relevance. |
| `ranking` | `merge_ranked()`. It sorts the candidates of all RAGs by score, removes repeated chunk IDs, removes the candidates below `min_score` and cuts the list to the number of chunks. It reports each omitted candidate with its reason: `duplicate`, `low_score` or `limit`. |
| `ports` | The `TokenCounter` protocol of the size ruler. |
| `tracing` | `no_tracing()` and `untraced()`, which turn off LangChain and LangSmith tracing for one call. |

The package depends only on numpy and `langchain-core`.

The package has no LLM code. It does not count tokens for an LLM, it does not build prompts and it does not check citations. See [ADR 0014](../../docs/adr/0014-retrieval-only-and-app-side-fitting.md).
