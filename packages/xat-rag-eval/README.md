# xat-rag-eval

The evaluation harness of xat-rag. See the [architecture, section 13](../../docs/architecture.md#13-evaluation) and the [development guide](../../docs/development.md#retrieval-gate). It calls no LLM and uses no judge model.

```
uv run xat-rag-eval run --setup eval/fixtures/setup --golden eval/fixtures/golden.jsonl --data build/out/eval-data --baseline eval/baseline.json
uv run xat-rag-eval run ... --write-baseline   # after an agreed change of quality
```

The options are these:

- `--setup`: a folder with `recipes/` and `documents/`.
- `--golden`: the golden set, in JSONL.
- `--data`: a new or empty folder for the indexes of the run. A folder with old files is refused.
- `--baseline`: the baseline JSON to compare with. The file is required, unless the run writes it.
- `--write-baseline`: writes the kinds of the report as the new baseline. It refuses when the invariant or determinism rules fail. It writes no timings.
- `--models` and `--lock`: the models folder and the lock file. The defaults are `models` and `build/models.lock.json`.

A golden set is JSONL. Each line is one case. Unknown fields are errors:

```json
{"id": "ports-docs", "context": "ask-docs", "rag": "docs", "kind": "markdown",
 "question": "Which port does the orders service use?",
 "relevant": [{"chunk_id": "docs:orders.md#h/orders-service/ports-and-health", "grade": 2}]}
```

- `question` is the question. It must have text.
- `rag` is the RAG whose candidates the stage metrics use. Ranks and RRF scores are per RAG.
- `relevant` needs at least one item. Each item has exactly one of `chunk_id` or `doc_id`. A `doc_id` item makes a document-level case, and any chunk of that document counts.
- The grade is from 1 to 3.
- Case IDs must be unique.

For each kind, the harness reports these values:

- Stage metrics for each RAG of the case: recall@10, nDCG@10 and MRR after each stage (lexical, dense, fused and reranked). The reranked stage is the order after the reranker, duplicate removal and MMR.
- Final metrics for the merged list of the context: the recall at 3, 5 and 10, the MRR of the final list, and the source coverage.

The harness checks that the final list has at most 10 chunks, that its chunk IDs are unique and that its scores do not go up. It also checks that the same input gives the same list. It reports p50 and p95 times for the retrieval and for the context request.

The [development guide](../../docs/development.md#retrieval-gate) gives the rules of the gate and of the chunk-boundary test.

The golden sets of an app are owned by the team that owns the app. That team builds 50 to 100 cases for each source kind, from sanitized real data. The fixture set in this repository only tests the harness.
