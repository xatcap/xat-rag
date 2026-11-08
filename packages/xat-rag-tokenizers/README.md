# xat-rag-tokenizers

The fixed size ruler of xat-rag. See [ADR 0014](../../docs/adr/0014-retrieval-only-and-app-side-fitting.md) and the [architecture, section 4](../../docs/architecture.md#size-ruler).

- `HfTokenCounter` counts tokens with a locked `tokenizer.json` of the bundled Ministral-3-3B tokenizer, through the `tokenizers` library. It is the size ruler of chunks. It has no link to the LLM of an app.
- It counts text without the special tokens. A text such as `[INST]` counts as text.
- The counts are used for `size_tokens` in each result and for the limit `max_chunk_tokens` of each source.

The package does not count tokens for an LLM. An app counts the tokens of its own LLM with the tokenizer of that LLM.
