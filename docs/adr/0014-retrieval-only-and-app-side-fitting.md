# 0014. Make xat-rag retrieval-only: the app fits the context, and xat-rag has no model knowledge

- Status: Accepted
- Date: 2026-11-09
- Deciders: Project maintainer
- Supersedes: [ADR 0007](0007-context-budget-and-token-counting.md) and [ADR 0008](0008-context-pack-and-citations.md). Partly supersedes [ADR 0006](0006-hybrid-retrieval-and-reranking.md) and [ADR 0010](0010-untrusted-content-privacy-and-ingestion.md).

## Context

The first design made xat-rag select the context for one LLM. It held a generator profile with the context window, the template overhead and the tokenizer of the LLM. It counted the tokens of each chunk. It packed the chunks into a budget with quotas for each source. It rendered the messages with trust blocks, and it built the citation grammar.

This design made xat-rag depend on one LLM. A change of LLM needed a new profile, a new tokenizer lock and a new recipe. The counts of the tokenizer could also differ from the count of the app's LLM, so the budget could still be wrong.

The owner decided that xat-rag returns retrieved text only. The app fits the text to its LLM, because the app knows the LLM and the prompt.

## Decision

- xat-rag returns the ranked chunks of a request, in rank order. Each chunk has its text, its header, its source, its metadata and a size count.
- xat-rag does not fit the chunks to a budget or a window. It has no context window, no reserved output, no `max_tokens`, no generator profile and no tokenizer of an LLM.
- xat-rag builds no prompt. It renders no messages, no system prompt, no pinned prompts, no labels and no citation grammar.
- xat-rag does not wrap, escape or label the text of a chunk. The app does this.
- xat-rag does not validate citations. The app checks the citations with the `chunk_id` and `source` values that it received.
- The source quotas (`min_tokens` and `max_share`), the packer and the settings of the window are removed.
- The `[context]` recipe has the fields `name`, `format`, `chunks` and `sources`. Each `[[context.sources]]` table has only the field `rag`. The `[limits].pack` value limits the merge of the RAG results.
- The bundled tokenizer of Ministral-3-3B stays as a locked file. It is a fixed size ruler, and its lock role is `size_tokenizer`. Its ID is `ministral-3-3b-tokenizer@<first 12 hex digits of its sha256>`. The ruler has no link to the LLM of the app.
- The counter keeps `encode_special_tokens = True`. A text such as `[INST]` counts as text.
- The chunker counts a section with the size ruler, and it splits the section with the same count. The splitter `RecursiveCharacterTextSplitter` uses the size ruler as its length function. xat-rag never uses a splitter that counts with tiktoken.
- The size count of each chunk is stored with the chunk, in the `token_count` column. The API shows it as `size_tokens`.
- The embedder limit of 510 tokens for each chunk is a separate rule from [ADR 0004](0004-onnx-runtime-and-locked-models.md). It is not changed.
- The manifest key `generator_tokenizer_id` is renamed to `size_tokenizer_id`. A manifest with the old key still loads. The index needs no rebuild.
- The conformance test with `mistral-common` and the measurement tool are removed.

## Consequences

- The app counts the tokens of each chunk with the tokenizer of its own LLM. It also chooses the chunks that fit. xat-rag cannot tell the app that a context is too large.
- A change of LLM needs no rebuild of the index and no change to the recipes.
- The API is smaller. Callers that used `rendered.messages`, the citation grammar or the validation route must change their code. See the [changelog](../../CHANGELOG.md).
- The app must treat all returned text as untrusted, and it must escape it. ADR 0010 gives the rule. The app must do this work now, because xat-rag no longer does it.
- The evaluation gate measures the final list of chunks. It no longer measures a packed context with a budget. See the [development guide](../development.md#retrieval-gate).
- xat-rag still calls no generator. This rule is from [ADR 0008](0008-context-pack-and-citations.md), and it stays in force.
- The size ruler is not the tokenizer of the app. A chunk that has 350 size tokens can have more or fewer tokens for the LLM of the app.

## Implementation notes (v0.1)

- The retrieval use case is `retrieve()` in `xat_rag/retrieve.py`. It returns a `RetrievalResult` with the chunks and the omitted candidates.
- `merge_ranked()` in `xat_rag_core/ranking.py` sorts the candidates of all RAGs by score, then by chunk ID. It removes repeated chunk IDs and cuts the list to the number of chunks.
- `RetrievedChunk.size_tokens` comes from the stored `token_count`. The `version` of a chunk comes from the `documents` table, through a read-side join. The index does not change for this.
- The lock role is `size_tokenizer`. The lock has no `tekken.json` file.
