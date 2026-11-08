# xat-rag-sqlite

The SQLite store of xat-rag. Each RAG is one SQLite file. See [ADR 0005](../../docs/adr/0005-sqlite-store-and-index-manifest.md).

| Module | What it does |
|---|---|
| `lexical` | Code-aware lexical tokens (`code-v1`) and safe FTS5 query expressions. |
| `manifest` | The index manifest, and the check that refuses a mismatch. It reads the old key `generator_tokenizer_id` as `size_tokenizer_id`. |
| `store` | `SqliteRagStore`: the write side (upsert, delete, checkpoint) and the search functions. |
| `retrievers` | `Fts5Retriever` (keyword search) and `VecRetriever` (vector search), which implement LangChain `BaseRetriever`. |

Rules:

- Each connection sets `PRAGMA secure_delete = ON`. The FTS5 index uses its `secure-delete` option.
- The file uses WAL mode. One writer runs at a time. Queries continue during ingestion.
- Each document is one transaction. A crash does not damage the index.
- xat-rag loads only the locked sqlite-vec extension, and then it turns extension loading off.
- All values, and the field names of the filters, are SQL parameters.
- SQLite 3.44 or later is required, for the FTS5 `secure-delete` option.
