# 0008. Return a context pack, and do not call a generator

- Status: Superseded by [ADR 0014](0014-retrieval-only-and-app-side-fitting.md)
- Date: 2026-11-07
- Deciders: Project maintainer

> This decision is superseded on 2026-11-09 by ADR 0014. xat-rag no longer returns a context pack, renders messages or validates citations. xat-rag still calls no generator. The text below is the history of the decision.

## Context

xat-rag can call a generator itself, or it can give the app everything that it needs to call the generator. If xat-rag calls the model, two components own cost, time limits and errors of generation.

## Decision

- xat-rag never calls a generator: not to write answers, not to rewrite queries, not to write chunk context, and not to judge quality in evaluation.
- xat-rag has no code that depends on a generator. The only shared item is the window size, which the app sets in the recipe and in the generator.
- `POST /v1/context` returns a context pack. The pack has the budget, `max_tokens`, the pinned items, the labelled chunks (C1 to Cn), the input, the rendered messages, the citation grammar and schema, and the omitted candidates with reasons. See [architecture, section 8](../architecture.md#7-result-of-a-context-request).
- `rendered.messages` uses the OpenAI message format.
- The answer format is `{"answer": "...", "citations": ["C1"]}`. The GBNF grammar allows only the labels in the pack. The app sends the grammar to the local model, and never the grammar and the schema together.
- xat-rag stores no packs. `POST /v1/citations/validate` takes the answer and the chunk list of the pack from the app. It checks the JSON and the labels, and maps each label to a `chunk_id` and a `uri`. For a PDF chunk, the `uri` ends with `#page=N`, the standard PDF open parameter. An answer cut at `max_tokens` is reported as not valid.
- A `debug` block with the scores of each stage is available on request.

## Consequences

- xat-rag and the generator stay independent. The generator stays the only owner of generation, cost and time limits.
- The app makes two calls (xat-rag, then the generator) and a third for validation.
- Techniques that need a generator (Contextual Retrieval with model-written context, query rewriting, judge-based evaluation) are not available in xat-rag. A change to this rule needs a new ADR.
- The app can change the messages before it sends them, for example to add its own instructions.
- Invented citation labels are found after generation.
