# 0013. Remove log handling and exact lookups from queries

- Status: Accepted
- Date: 2026-11-09
- Deciders: Project maintainer

## Context

The first design had two query paths for specific input. The `log-v1` builder masked log lines, grouped them into templates and extracted identifiers such as exception names, JIRA keys and stack frames. The `ExactLookupRetriever` then matched these identifiers in the metadata. Its result was a third ranked list for RRF.

These paths put the rules of one input format into xat-rag. A log format belongs to the app that owns the logs. The paths also needed their own limits, their own tests and their own request fields. [ADR 0014](0014-retrieval-only-and-app-side-fitting.md) makes xat-rag a retrieval service that does not know the input of the app. This decision is part of that change.

## Decision

- xat-rag has one query path. It takes a plain question and builds the query with `build_query()`.
- xat-rag has no log-specific code. It does not mask, group or parse log lines.
- xat-rag has no exact lookups. The `ExactLookupRetriever`, the `exact_k` recipe key, the `exact_rank` debug field and the `exact_lookups` query field are removed.
- RRF merges two ranked lists, the keyword search and the vector search.
- Identifiers such as error codes, file paths, JIRA keys and commit SHAs are found by the keyword search. The code-aware lexical tokens keep a compound name whole, and they also add its parts. For example, `PROJ-123` gives `proj_123`, `proj` and `123`.
- The request fields `log_lines` and `selected_lines` are removed. The field `builder` is removed from the search and query routes.
- The package `xat-rag-logs` is removed.
- The lexical token rules are not changed, so the index needs no rebuild for this decision.

## Consequences

- The query path is smaller. There are fewer limits, fewer tests and fewer request fields.
- A question that names an identifier now relies on the keyword search and the vector search only. An exact lookup no longer adds the chunk to the candidates. A rare identifier can rank lower than before.
- The fixture golden set has a small number of identifier and near-match cases. The fixture corpus is small, so a comparison on a larger golden set is still necessary to measure this effect. See the [open items](../architecture.md#16-later-versions-and-open-items).
- An app that needs log-like search must prepare the question itself. It can remove noise, and it can put the identifiers first. See the [app guide](../app-guide.md).
- Filters are still in the store. No recipe key and no route sets them. See [architecture, section 3](../architecture.md#3-ports).
- A change to the rules of the lexical tokens needs a rebuild, as before. See [ADR 0005](0005-sqlite-store-and-index-manifest.md).
