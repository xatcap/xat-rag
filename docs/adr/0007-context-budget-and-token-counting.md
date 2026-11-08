# 0007. Let the context recipe own the budget, and count tokens with the generator's tokenizer

- Status: Superseded by [ADR 0014](0014-retrieval-only-and-app-side-fitting.md)
- Date: 2026-11-07
- Deciders: Project maintainer

> This decision is superseded on 2026-11-09 by ADR 0014. xat-rag no longer owns a budget, a window or a generator profile. The tokenizer of Ministral-3-3B stays as a fixed size ruler, and ADR 0014 keeps the rules for that ruler. The text below is the history of the decision.

## Context

A context pack that is too large makes the generator fail or cut the prompt. A pack that is too small wastes the small window. The count must use the tokenizer of the generator. xat-rag never calls a generator (see [ADR 0008](0008-context-pack-and-citations.md)), so it must count tokens itself. In v1, only the local model is a generator.

## Decision

- **The context recipe owns the window size and the split.** A request can lower the values, but not raise them.
- **The window must match the generator:** the context size that the generator has for one request. The app keeps the recipe window and the generator context size equal.
- **A generator profile** describes the window, the tokenizer, the template overhead, the overhead for each message and a safety margin of 3%. v1 has one profile: `ministral-3-3b`. A recipe with another generator is refused.
- **The tokenizer of Ministral-3-3B is bundled** as a locked file from the same revision as the model. The `tokenizers` library reads it, or a small reader for the Tekken format.
- **A conformance test** in CI compares our counts with `mistral-common` (Apache-2.0), Mistral's reference tokenizer, on a fixed text set. The counts must be equal. `mistral-common` is a dev dependency only.
- **The overheads are measured.** A dev tool encodes sample chats with the `mistral-common` chat template, and stores the values in the profile.
- **The splitter uses the same count.** `RecursiveCharacterTextSplitter` gets our `TokenCounter` as its `length_function`. xat-rag never uses a splitter that counts with tiktoken.
- **The budget for chunks** is: window, minus reserved output, minus template overhead, minus the overhead of each message, minus pinned items, minus input, minus margin. The packer counts each chunk with its wrapper.
- **The pack gives `max_tokens`**, equal to the reserved output. The app sends this value to the generator.
- **Pinned prompts** are never cut, pruned or moved. If pinned items and input do not fit, the result is `context_too_large_error` (422).
- **Quotas:** each source has `min_tokens` and `max_share`. Unused tokens go back to the pool. If the budget is smaller than the sum of the floors, all floors go down in proportion.
- **Order:** pinned items first. Then the strongest chunk first and the second strongest last. Then the input and the question at the end.

## Consequences

- The pack never exceeds the window when the recipe and the generator agree.
- The generator does not report its context size, so xat-rag cannot detect a difference. The app must keep the recipe window and the generator context size equal.
- A change of the local model needs a new generator profile and a new tokenizer lock.
- Cloud generators need their own profiles. This is later work.
- xat-rag needs no generator, no llama.cpp and no model weights to count tokens or to run its tests.

## Implementation notes (v0.1)

- The profile `ministral-3-3b` has `context_window` 8192, `template_overhead` 2 and `per_message_overhead` 3. These values were measured with `mistral-common` 1.13.1 on 2026-11-08. The BOS token costs 1 token, and each message wrapper costs 2 tokens. Each value adds 1 token of margin.
- The renderer overhead counts the trust note, and each separator alone. A tokenizer can merge a run of separators, so each one is counted alone.
