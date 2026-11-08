# 0006. Use hybrid search with RRF and a cross-encoder reranker, without query rewriting

- Status: Accepted
- Date: 2026-11-07
- Deciders: Project maintainer

## Context

The window for retrieved chunks holds only about 10 to 18 chunks. Technical text has many exact identifiers, such as error codes, paths, JIRA keys and SHAs. Dense search does not match them well. Small models make query rewriting less reliable. See the [research](../research.md).

## Decision

- Each RAG runs three LangChain `BaseRetriever` classes: `Fts5Retriever` (BM25 on FTS5, with code-aware tokens), `VecRetriever` (sqlite-vec) and `ExactLookupRetriever`. The first two give their top 40 by default. Each retriever gets its own query text.
- Exact lookups from the query (for example JIRA keys and stack-frame paths) give a third ranked list for RRF. They add candidates, and they never remove candidates.
- Hard metadata filters come only from the recipe or the request. They apply inside each search, before the top-k cut.
- Our own Reciprocal Rank Fusion with k=60 merges the lists, with ties broken by `chunk_id`. LangChain's `EnsembleRetriever` is not used, because it sends the same text to every retriever and returns no ranks for each stage.
- The use case calls each stage itself. It does not use an LCEL pipeline.
- A cross-encoder (`OnnxCrossEncoderCompressor`, a LangChain `BaseDocumentCompressor`) reranks the fused candidates (40 by default) and keeps the top N for each RAG. All RAGs in one context use the same reranker, so that their scores can be compared.
- MMR and duplicate removal run before packing.
- A recipe can turn off a stage only by an explicit value, for example `reranker = "none"`. A failed stage is an error, never a silent skip.
- Query rewriting, HyDE and chunk context written by a model are not in v1. Queries come from deterministic builders, such as the LogQueryBuilder.

## Consequences

- Exact identifiers and paraphrased text both find their chunks.
- Reranking with MiniLM takes about 0.2 to 0.4 s on CPU for 40 candidates (an estimate). A context with three RAGs reranks about 120 candidates, so about 0.6 to 1.2 s. This is inside the NFR-7 target of 1.5 s. The evaluation harness measures the real value.
- The pipeline makes no call to a generator, so it is fast and deterministic.
- A question with vague words can retrieve less well without rewriting. We measure this with the golden sets before we add rewriting.

## Implementation notes (v0.1)

- The `[retrieval]` table of a RAG recipe sets the values. The defaults are 40 lexical, 40 dense and 20 exact candidates, RRF with k 60, 40 reranked candidates, `keep` 8 and an MMR lambda of 0.7.
- Duplicates are removed by content hash, or by a cosine similarity of 0.95 or more.
- The reranker keeps all of its candidates in score order. MMR then chooses the `keep` chunks.
- The reranker is off when the recipe sets `reranker = "none"`. This is the default.
- Query rewriting, HyDE and neighbor expansion are not in v0.1.
