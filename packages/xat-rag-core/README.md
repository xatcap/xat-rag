# xat-rag-core

The domain model, the ports and the rules of xat-rag. See the [architecture](../../docs/architecture.md).

| Module | What it does |
|---|---|
| `errors` | The error types and their HTTP status codes. |
| `model` | `SourceDocument`, `Chunk`, the span types, `Candidate`, `RawDocument` and the parse result types. |
| `ids` | Content hashes, chunk IDs, heading anchors and slugs. `log_id` makes a hashed ID for logs. No code uses it yet. |
| `parsing` | `SectionParser` (the protocol of the parsers), `Deadline`, `decode_text` and `source_document`. |
| `markdown_form` | `SourceBlock`, the typed blocks that readers make. `render_markdown()` makes the one Markdown form of a document from its blocks. `build_sections()` makes one section for each heading. |
| `spans` | `span_to_meta()` and `span_from_meta()`, which store the span of a chunk as metadata. |
| `query` | `RetrievalQuery` and `build_query()`. The query has the lexical terms, the dense text and the rerank text. |
| `sanitize` | Removes control, invisible and bidirectional characters, and normalizes to NFC. |
| `fusion` | Reciprocal Rank Fusion with `chunk_id` tie-breaks. |
| `mmr` | Duplicate removal and Maximal Marginal Relevance. |
| `ranking` | `merge_ranked()`. It sorts the candidates of all RAGs by score, removes repeated chunk IDs, removes the candidates below `min_score` and cuts the list to the number of chunks. It reports each omitted candidate with its reason: `duplicate`, `low_score` or `limit`. |
| `ports` | The protocols `TokenCounter`, `Embedder`, `Retriever` and `Reranker`. |

The package depends only on numpy.

The package has no LLM code. It does not count tokens for an LLM, it does not build prompts and it does not check citations.
