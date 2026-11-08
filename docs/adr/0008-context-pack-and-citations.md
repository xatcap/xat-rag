# 0008. Return a context pack, and have no integration with xat-llm-engine

- Status: Accepted
- Date: 2026-11-07
- Deciders: Project maintainer

## Context

xat-rag can call xat-llm-engine itself, or it can give the app everything that it needs to call the engine. If xat-rag calls the model, two components own cost, time limits and errors of generation.

## Decision

- xat-rag has no integration with xat-llm-engine. It never calls the engine or another generator: not to write answers, not to rewrite queries, not to write chunk context, and not to judge quality in evaluation.
- xat-rag has no code that depends on the engine. The only shared items are conventions (lifecycle, error body, writing style) and the window size, which the app sets in both.
- `POST /v1/context` returns a context pack. The pack has the budget, `max_tokens`, the pinned items, the labelled chunks (C1 to Cn), the input, the rendered messages, the citation grammar and schema, and the omitted candidates with reasons. See [architecture, section 8](../architecture.md#8-the-context-pack).
- `rendered.messages` uses the OpenAI message format, which the local provider of the engine accepts.
- The answer format is `{"answer": "...", "citations": ["C1"]}`. The GBNF grammar allows only the labels in the pack. The app sends the grammar to the local model, and never the grammar and the schema together.
- xat-rag stores no packs. `POST /v1/citations/validate` takes the answer and the chunk list of the pack from the app. It checks the JSON and the labels, and maps each label to a `chunk_id` and a `uri`. For a PDF chunk, the `uri` ends with `#page=N`, the standard PDF open parameter. An answer cut at `max_tokens` is reported as not valid.
- A `debug` block with the scores of each stage is available on request.

## Consequences

- xat-rag and the engine stay independent. The engine stays the only owner of generation, cost and time limits.
- The app makes two calls (xat-rag, then the engine) and a third for validation.
- Techniques that need a generator (Contextual Retrieval with model-written context, query rewriting, judge-based evaluation) are not available in xat-rag. A change to this rule needs a new ADR.
- The app can change the messages before it sends them, for example to add its own instructions.
- Invented citation labels are found after generation.

## Implementation notes (v0.1)

- `POST /v1/citations/validate` takes `answer` and `chunks` (each with a `label` and a `chunk_id`). It returns `valid`, `problem`, the citations with their URIs, `unknown_labels` and `missing_chunks`.
- The validator has no input for the `finish_reason` of the engine. An answer that the engine cut at `max_tokens` is not valid JSON. Its problem is `not_json`.
- The problems are `not_json`, `bad_shape`, `unknown_label` and `missing_chunk`.
- The pack has `debug` only when the request sets `debug: true`.
- The pack always has `json_schema`. The app decides which one to send. It sends the grammar to a generator that accepts one.
