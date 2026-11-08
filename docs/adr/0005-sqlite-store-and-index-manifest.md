# 0005. Store each RAG in one SQLite file with a manifest

- Status: Accepted
- Date: 2026-11-07
- Deciders: Project maintainer

## Context

The target is about 100k chunks for each laptop. At that size, brute-force vector search is fast enough. For example, 100k vectors of 384 float32 values need about 150 MB. A desktop app needs a store that is one file, with atomic writes and simple backup.

## Decision

- Each RAG has its own SQLite file in the data folder. The file holds the documents, the chunks, an FTS5 lexical index, a sqlite-vec vector index and the manifest.
- SQLite runs in WAL mode with one writer. Queries continue during ingestion.
- A delete removes the document from all searches at once. To remove the deleted bytes from the file, each connection sets `PRAGMA secure_delete=ON`, each FTS5 index uses its `secure-delete` option, and a `wal_checkpoint(TRUNCATE)` runs after each delete batch. The implementation must check how sqlite-vec handles deleted vectors. See [architecture, section 14](../architecture.md#14-privacy-and-untrusted-content).
- Three LangChain `BaseRetriever` classes read the file: `Fts5Retriever`, `VecRetriever` and `ExactLookupRetriever`. A custom `DocumentStore` owns all writes, the manifest and the migrations. Another store, for example LanceDB, can replace SQLite for larger scales.
- LangChain's `SQLiteVec` and `BM25Retriever` are not used. See [ADR 0001](0001-langchain-core-as-foundation.md).
- The version of sqlite-vec is pinned, because it is before version 1.0.
- The manifest records the index format, the embedder and its sha256, the dimensions, the lexical tokenizer version, the chunker versions and a hash of the recipe fields that change the index, and the versions of `langchain-text-splitters` and of the parser libraries. See [architecture, section 4](../architecture.md#4-data-model).
- If the manifest does not match the loaded models, chunkers or recipe, xat-rag refuses the index with `index_mismatch_error` (409). The caller starts a rebuild.

## Consequences

- One RAG can be rebuilt, deleted or backed up without an effect on the others.
- A query that uses several RAGs opens several files and merges the results.
- The index format is a SemVer contract. A change to it is a breaking change, unless the release ships a migration.
- An upgrade of sqlite-vec can need a rebuild. The release notes tell when.
- Above about 1M chunks, the store must change to an ANN index.
- Software cannot remove all copies of deleted data (SSD wear leveling, journals, backups). To remove a whole RAG, delete its file.

## Implementation notes (v0.1)

- The key `schema_version` in the `meta` table holds `1`. A file with another schema version gives `index_mismatch_error`.
- The sqlite-vec table uses cosine distance.
- The check of how sqlite-vec handles deleted vectors is not done yet. See the [open items](../architecture.md#16-later-versions-and-open-items).
