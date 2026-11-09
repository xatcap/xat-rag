# Development

This page describes how to set up xat-rag, and how to build, test, evaluate, package and release it. Read the [architecture](architecture.md) and the [Python guidelines](portable/python/index.md) first.

## Setup

xat-rag uses [uv](https://docs.astral.sh/uv/) and a workspace of packages. The Python version is in `.python-version` (3.13). The code needs Python 3.12 or later. The Python build must be able to load SQLite extensions. For this reason, uv uses only the Python builds that it manages. The setting is `python-preference` in `pyproject.toml`. If you made your `.venv` with a system Python, delete it and run `uv sync --locked` again.

1. Install the locked dependencies:

   ```bash
   uv sync --locked
   ```

2. Get the model files. They are not in git. The script checks each file against `build/models.lock.json`:

   ```bash
   uv run python tools/fetch_models.py
   ```

   The files go to `models/`. The folder `models/` is in `.gitignore`, except for its README.

The tests need the model files. Run step 2 before `pytest`.

## Commands

| Task | Command |
|---|---|
| Format check | `uv run ruff format --check packages tools build` |
| Lint | `uv run ruff check packages tools build` |
| Types (strict) | `uv run pyright` |
| Import rules | `uv run lint-imports` |
| All tests | `uv run pytest` |
| Dependency check | `uv run python tools/check_dependencies.py` |
| Retrieval gate | `uv run xat-rag-eval run --setup eval/fixtures/setup --golden eval/fixtures/golden.jsonl --data <new or empty folder> --baseline eval/baseline.json` |
| Frozen service | `uv run python build/release.py package` |
| Check the frozen folder | `uv run python build/release.py check-bundle` |
| Smoke test of the frozen service | `uv run python build/release.py smoke-test` |

Pytest runs with sockets blocked (`--disable-socket --allow-unix-socket`, see `pyproject.toml`). A test that needs loopback allows it itself.

Some checks run only where they are useful:

- The service and library tests in a new network namespace with only loopback. This runs on Linux only. The loopback interface must be up:

  ```bash
  sudo unshare --net -- sh -c "ip link set lo up && sudo -E -u $USER env PATH=$PATH .venv/bin/python -m pytest packages/xat-rag-service packages/xat-rag -p no:cacheprovider"
  ```

## Tools

| Script | What it does | Shipped |
|---|---|---|
| `tools/fetch_models.py` | Gets the model files from the Hugging Face hub and checks the size and sha256 of each file. With `--write-lock`, it reads `build/models.sources.json`, gets the files and writes `build/models.lock.json`. Use `--write-lock` only for a reviewed model change. | No |
| `tools/check_dependencies.py` | Compares the runtime packages of `uv.lock` with `build/dependencies.allow.json`. It follows the extras of each package, and it finds the workspace packages from the lock source. It fails on a package on the denylist, on a copyleft license (even next to a permissive one), on a license that is unknown, and on a new, removed or changed package. `--write` refuses to write when the check has problems. Use `--write` only after a reviewed change. | No |

The denylist and the license rules are in `tools/check_dependencies.py`. The allowlist is in `build/dependencies.allow.json`. The denylist includes `openai`, `posthog`, `tiktoken`, `torch`, `transformers`, `langchain-community` and `langchain-classic`.

## Retrieval gate

`xat-rag-eval` runs a golden set against the fixture corpus. The fixture set is in `eval/fixtures/setup`. It has three RAGs (`docs`, `jira` and `diffs`) and two context recipes (`ask-docs` and `triage`). The golden set is `eval/fixtures/golden.jsonl`.

Each golden case has these fields. Unknown fields are errors.

- `id`: unique in the set.
- `context` and `rag`: the context recipe, and the RAG whose candidates the stage metrics use. Ranks and RRF scores are per RAG, so the stage metrics measure one RAG only.
- `kind`: the source kind, used for the report.
- `question`: the question. It must have text.
- `relevant`: at least one item. Each item has exactly one of `chunk_id` or `doc_id`, and a `grade` from 1 to 3. A case scores chunks, or it scores documents. It cannot mix both.

The command needs a baseline file. The `--data` folder must be new or empty, so that old indexes do not change the result. The command prints a JSON report. It exits with 1 when the gate fails.

The report has two kinds of metrics:

- **Stage metrics.** For each RAG of a case, the report has the recall at 10, the nDCG at 10 and the MRR. It has them after each stage: `lexical`, `dense`, `fused` and `reranked`. The `reranked` stage is the order after the reranker, duplicate removal and MMR.
- **Final metrics.** For each kind, the report has the recall at 3, 5 and 10 of the final list of the context request. It also has the MRR of the final list and the source coverage. The final list is the merge of all RAGs of the context, cut to 10 chunks. For a document-level case, the recall counts the documents in the first N chunks. The source coverage is the share of cases in which the case RAG gives at least one chunk to the final list.

The gate fails closed. It fails on these problems:

- An invariant problem in the final list of a case. The list has more than 10 chunks, a chunk ID that repeats, or a score that is higher than the score before it.
- A final list that changes between two runs of the same case.
- A kind that is in only one of the report and the baseline.
- A drop of more than 2 points (200 basis points) in the recall at 10 of the `reranked` stage of a kind.
- A drop of more than 2 points (200 basis points) in the final recall at 5 or at 10 of a kind. A drop of exactly 2 points passes.
- A drop of more than 10 points (1000 basis points) in the final MRR of a kind.

The final recall at 3 and the source coverage are reported only. They do not fail the gate.

`--write-baseline` writes a new baseline after an agreed change. It refuses to write when the invariant or determinism problems exist. It writes only the `kinds` object, without timings. Then review the diff of `eval/baseline.json`.

Known limit: one `eval/baseline.json` is shared by the three operating systems. The int8 ONNX numbers can differ a little by CPU and by batch. A per-system baseline can be needed later.

The evaluation harness calls no LLM. See the [architecture](architecture.md#13-evaluation), section 13.

### Chunk-boundary test

`packages/xat-rag-eval/tests/test_chunk_boundaries.py` chunks the fixture corpus. It compares the chunk IDs, spans and content hashes with `eval/chunk_boundaries.json`. A parser or splitter upgrade that moves a boundary fails this test.

After a reviewed change, write the file again:

```bash
XATRAG_WRITE_BOUNDARIES=1 uv run pytest packages/xat-rag-eval/tests/test_chunk_boundaries.py
```

Then add the rebuild to the release notes. The manifest makes the old indexes need a rebuild.

## Run the service from source

The service needs the settings in [API reference](api.md#settings). For a local run, set these variables and start the service:

```bash
export XatRag__DataFolder="$PWD/build/out/dev-data"
export XatRag__RecipesFolder="$PWD/eval/fixtures/setup/recipes"
export XatRag__ModelsFolder="$PWD/models"
export XatRag__LockFile="$PWD/build/models.lock.json"
export XatRag__AccessKey="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
export XatRag__ReadyFile="$XatRag__DataFolder/ready.json"
uv run xat-rag-service
```

When the service is ready, the ready file holds its URL. Read the `url` field from that file, and send the access key in each request.

## Package and release

`build/release.py` has three tasks:

- `package` runs PyInstaller with `build/xat-rag-service.spec`. The output is one folder, `build/out/dist/xat-rag-service/`. It contains the executable and the libraries. The models are not in the folder. The app installs them next to it. The spec excludes the modules of ADR 0011, section 4, and the optional imports that the service does not use, such as PIL, setuptools and `pkg_resources`.
- `check-bundle` fails when a denied module is in the frozen folder. It checks the folder, the native libraries and the modules in the archive.
- `smoke-test` starts the frozen executable with the fixture setup. It ingests `eval/fixtures/setup/documents/docs/orders.md` with the access key, waits for the job, and asks the `ask-docs` context about the port of the orders service. The test passes when the first chunk has the expected chunk ID and source document. The service has 300 seconds to start. The stderr output goes to a file in the test folder. If the service does not start in time, the test stops it and fails.

[ADR 0002](adr/0002-python.md#measured-v01) records the measured size and start times.

The repository has no signing or notarization step yet. The first release task must add it, as [ADR 0002](adr/0002-python.md) states. The models are a separate release file. The lock file lists the license of each model.

The runtime hook `build/pyinstaller_runtime_hook.py` runs before `main`. It calls `harden_environment` only, and it keeps the access key for `main`. Then `main` calls `harden_process`, which reads and removes the key and installs the network guard. See [ADR 0011](adr/0011-offline-hardening.md).

## Continuous integration

The workflow is `.github/workflows/ci.yml`. It runs on each push to `main` and on each pull request. All jobs use the bash shell. The jobs install with `uv sync --locked`. The model files are cached with the key of `build/models.lock.json`.

The `check` job runs on Ubuntu, macOS 15 and Windows, with a timeout of 45 minutes:

1. `uv sync --locked` and `tools/fetch_models.py`.
2. `ruff check` and `ruff format --check` on `packages`, `tools` and `build`.
3. `pyright`.
4. `lint-imports`.
5. `pytest`, which includes the chunk-boundary test.
6. `tools/check_dependencies.py`.
7. The retrieval gate with `eval/baseline.json`.

The `offline` job runs on Ubuntu after `check`, with a timeout of 30 minutes. It runs the service and library tests in a new network namespace (`sudo unshare --net`). The job brings the loopback interface up, so the tests can use loopback only. It sets `LANGSMITH_TRACING`, `LANGSMITH_API_KEY` and `LANGSMITH_RUNS_ENDPOINTS`. A connection outside loopback fails in this job. The job uses no packet capture yet.

The `package` job runs on Ubuntu, macOS 15 and Windows after `check`, with a timeout of 45 minutes. It runs `package`, then `check-bundle`, then `smoke-test`.

## Adding a document kind or a model

Read the [architecture](architecture.md#15-extension-points), section 15, first. Each change needs these steps:

1. Add or change the code and its tests.
2. Update the recipe schema and the manifest version, if the index changes. A change to the index needs a rebuild note in the [changelog](../CHANGELOG.md).
3. Run the commands in this page.
4. Update the [API reference](api.md) and the [architecture](architecture.md) when a route, a setting or an error changes.
5. Add or update an ADR when the decision changes.
