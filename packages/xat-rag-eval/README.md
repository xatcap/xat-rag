# xat-rag-eval

The evaluation harness of xat-rag. See the [architecture, section 13](../../docs/architecture.md#13-evaluation) and the [development guide](../../docs/development.md#retrieval-gate). It calls no generator and uses no judge model.

```
uv run xat-rag-eval run --setup eval/fixtures/setup --golden eval/fixtures/golden.jsonl --data build/out/eval-data --baseline eval/baseline.json
uv run xat-rag-eval run ... --write-baseline   # after an agreed change of quality
```

The options are these:

- `--setup`: a folder with `recipes/`, and optionally `prompts/` and `documents/`.
- `--golden`: the golden set, in JSONL.
- `--data`: a new or empty folder for the indexes of the run. A folder with old files is refused.
- `--baseline`: the baseline JSON to compare with. The file is required, unless the run writes it.
- `--write-baseline`: writes the kinds of the report as the new baseline. It refuses when the budget or determinism rules fail. It writes no timings.
- `--models` and `--lock`: the models folder and the lock file. The defaults are `models` and `build/models.lock.json`.

A golden set is JSONL. Each line is one case. Unknown fields are errors:

```json
{"id": "npe-1", "context": "log-triage", "rag": "jira", "kind": "jira", "log_lines": ["..."],
 "relevant": [{"chunk_id": "jira:PROJ-123.json#desc", "grade": 2}]}
```

- `rag` is the RAG whose candidates the stage metrics use. Ranks and RRF scores are per RAG.
- `relevant` needs at least one item. Each item has exactly one of `chunk_id` or `doc_id`. A `doc_id` item makes a document-level case, and any chunk of that document counts.
- The grade is from 1 to 3.
- Case IDs must be unique.

For each kind, the harness reports recall@10, nDCG@10 and MRR after each stage (lexical, dense, exact, fused and reranked), and the pack recall. The reranked stage is the order that the packer gets. The harness checks that each pack fits the window minus the reserved output and the safety margin, when it is counted again on the rendered messages. It also checks that the same input gives the same pack. It reports p50 and p95 times for the retrieval and for the composition.

The gate fails closed. It fails on a budget or determinism problem, and on a kind that is in only one of the report and the baseline. It fails when the reranked recall@10 or the pack recall of a kind drops by more than 2 points (200 basis points) below the baseline (NFR-14). A drop of exactly 2 points passes.

The chunk-boundary test is `tests/test_chunk_boundaries.py`. It compares the chunks of the fixture corpus with `eval/chunk_boundaries.json`. Write the file again with `XATRAG_WRITE_BOUNDARIES=1` only after a reviewed change.

The golden sets of the log-viewer are owned by the log-viewer team. The fixture set in this repository only tests the harness.
