# xat-rag-logs

The log query builder of xat-rag (`log-v1`). It turns log lines into a retrieval query, in a deterministic way. See the [architecture, section 6](../../docs/architecture.md#6-query-and-compose-flow).

1. Cut each line to 4096 characters. Remove ANSI codes. Extract identifiers. Mask timestamps, UUIDs, hex values, IP addresses and numbers.
2. Group equal masked lines into templates, with a count and one raw example for each.
3. Fill the query: identifiers for BM25, templates for the dense search, lookups for `jira_key` and `path_name`.

`fit_input` builds the input section within a token cap. It drops the templates with the lowest rank first: selected lines rank highest, then `ERROR` and `FATAL` lines or lines with an identifier, then `WARN`, then the rest, rare before frequent.
