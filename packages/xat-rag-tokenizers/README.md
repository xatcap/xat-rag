# xat-rag-tokenizers

Generator token counters and generator profiles. See [ADR 0007](../../docs/adr/0007-context-budget-and-token-counting.md).

- `HfTokenCounter` counts tokens with a locked `tokenizer.json` of the generator, through the `tokenizers` library. It counts text without the special tokens. A text such as `[INST]` counts as text.
- `PROFILES` holds the generator profiles. v1 has one: `ministral-3-3b`. Its overheads were measured with `tools/measure_profile.py`, which uses `mistral-common` as a development tool.
- `tools/measure_profile.py --check` compares the counts with `mistral-common` on a fixed text set. The `check` job of CI runs it.

xat-rag refuses a recipe with a generator that has no profile. The refusal happens at startup.
