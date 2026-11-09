# 0017. Set a minimum score for each context

- Status: Accepted
- Date: 2026-11-09
- Deciders: Project maintainer
- Related: [ADR 0006](0006-hybrid-retrieval-and-reranking.md) and [ADR 0014](0014-retrieval-only-and-app-side-fitting.md). This decision amends the `[context]` fields and the merge of ADR 0014.

## Context

A context request always returns the best chunks it finds. When no chunk matches the question, the result still holds the least poor chunks. The app then receives text that does not answer the question.

The reranker gives each candidate a score. The score is the raw output of the reranker model `ms-marco-MiniLM-L6-v2`. It is not a probability. Its range depends on the reranker model.

[ADR 0006](0006-hybrid-retrieval-and-reranking.md) requires one reranker for each context. All RAGs of a context then share one scale. A cut-off on the score therefore belongs to the context.

## Decision

- The `[context]` table of a recipe can set `min_score`. It is a finite number. It has no default, so no score filter applies.
- The merge omits a candidate with a score below `min_score`. The reason is `low_score`. A candidate with a score equal to `min_score` is kept.
- The merge checks each candidate in this order: a repeated chunk ID, then `min_score`, then the number of chunks.
- The RAGs of the context need a reranker. RRF scores are always positive, so a context with no reranker cannot use the filter. xat-rag refuses such a recipe at start.
- A value that is not a finite number is refused at start. Booleans, strings, `inf` and `nan` are refused.
- A request cannot set `min_score`. A request body with this field gets 400.
- `POST /v1/rags/{rag}/search` is not filtered.
- The `debug` list shows the candidates after `keep` and MMR. It includes the candidates that the filter omits.
- The default contexts `ask-docs`, `ask-pdfs` and `triage` set `min_score = -5`.
- The filter reads the recipe only. A change of `min_score` needs no index rebuild.

The value -5 was measured on the fixture corpus. The 16 relevant golden items have scores from -1.675 to 9.62. Five fixed unrelated questions, each asked in `ask-docs` and in `triage`, have a top score of about -11. The value -5 keeps all relevant items and removes the unrelated ones.

A value of 0 was rejected. It drops two correct JIRA answers, which score -0.61 and -1.68.

One PDF sample scored 8.585 for a related question and -11.30 for an unrelated one. The golden set has no PDF case. The PDF check is one manual check, so it does not show that -5 fits all PDFs.

## Consequences

- A context request can return an empty `chunks` list with status 200. It has two causes. Either the RAGs hold no matching chunks, or every candidate scored below `min_score`. The `omitted` list tells the causes apart. An app with an exhaustive match on the omitted reasons must add `low_score`.
- The default contexts can return fewer chunks or no chunks. Apps with their own recipes are unchanged, unless they add the `min_score` key.
- The value depends on the reranker model. A change of reranker model needs a new measurement of the values. No check in xat-rag detects a value that is stale after a reranker change.
- `score` is not a confidence. It is a raw value of the reranker model, and it has no upper or lower bound.
- MMR and `keep` run for each RAG before the filter. A chunk that they remove is not considered again, so a result can be shorter than necessary.
- The retrieval gate measures the final list after the filter.

## Alternatives considered

- **A value of 0.** Rejected. It drops two correct JIRA answers, as described above.
- **A `min_score` field in the request.** Not chosen. The value depends on the reranker of the context, so the owner of the recipe sets it. A caller could otherwise set a value that does not fit the reranker.
- **A filter in the app.** Not chosen. Each app would need to know the scale of the reranker and repeat the rule.
