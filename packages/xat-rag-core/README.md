# xat-rag-core

The domain model, the ports and the pipeline rules of xat-rag. See the [architecture](../../docs/architecture.md).

| Module | What it does |
|---|---|
| `errors` | The error types and their HTTP status codes ([ADR 0009](../../docs/adr/0009-error-model.md)). |
| `model` | `SourceDocument`, `Chunk`, the span types, `Candidate`, `RawDocument` and the parse result types. |
| `ids` | Content hashes, chunk IDs, heading anchors and slugs. `log_id` makes a hashed ID for logs. No code uses it yet. |
| `parsing` | `SectionParser` (the protocol of the parsers), `Deadline`, `decode_text` and `SectionBlobParser`, a LangChain adapter. |
| `lc_codec` | The only code that reads or writes LangChain `Document.metadata`. |
| `query` | `RetrievalQuery`, exact lookups, filters and the text query builder (`text-v1`). |
| `sanitize` | Removes control, invisible and bidirectional characters, and normalizes to NFC. |
| `fusion` | Reciprocal Rank Fusion with `chunk_id` tie-breaks. |
| `mmr` | Duplicate removal and Maximal Marginal Relevance. |
| `budget` | Generator profiles and the token budget for chunks. |
| `packer` | Quotas for each source, and the "edges" order. |
| `render` | The chat messages, with escaped untrusted blocks. |
| `citations` | The GBNF grammar, the JSON schema and the answer check. |
| `ports` | The `TokenCounter` and `QueryBuilder` protocols. |
| `tracing` | `no_tracing()` and `untraced()`, which turn off LangChain and LangSmith tracing for one call. |

The package depends only on numpy and `langchain-core`.
