# 0002. Write xat-rag in Python

- Status: Accepted
- Date: 2026-11-07
- Deciders: Project maintainer

> Note (2026-11-09): The log query builder that this ADR names was removed. See [ADR 0013](0013-remove-log-handling-and-exact-lookups.md).

## Context

xat-rag needs good retrieval tools, and it can use any stack. The apps call xat-rag through a loopback HTTP API (see [ADR 0003](0003-library-and-loopback-service.md)), so the language of the app does not matter.

The hard part of xat-rag is retrieval quality: chunkers, the log query builder and the evaluation gate. The evaluation must run in the same language as the pipeline that it measures.

| Concern | Python 3.12+ | .NET | Rust |
|---|---|---|---|
| Retrieval and evaluation tools | The best: `tokenizers`, `onnxruntime`, `sqlite-vec` wheels, tree-sitter wheels, IR metric libraries, model export tools. | Medium: official ONNX Runtime and `Microsoft.ML.Tokenizers`. No maintained tree-sitter binding, no log template library, weak evaluation tools. | Strong libraries, but the slowest development. |
| SQLite extensions | `sqlite3` in the standard library. The interpreter must allow `enable_load_extension`. python-build-standalone allows it. Some system builds do not. | `Microsoft.Data.Sqlite` has FTS5. We ship sqlite-vec for each platform. | Native. |
| Package size and start time | A frozen folder of about 120 to 200 MB, plus models. Start time 1 to 3 s. | Self-contained, about 70 to 100 MB. Start time below 0.5 s. | About 30 to 60 MB. |
| Signing | Many native libraries to sign. macOS notarization needs care. | Standard .NET signing. | Simple. |

## Decision

Write xat-rag in Python 3.12 or later. Use the baseline in the [Python guidelines](../python/index.md): a uv workspace, `ruff`, `pyright` in strict mode and `pytest`.

Freeze the service with PyInstaller (one folder) on python-build-standalone. Change to Nuitka only if size or start time is a problem.

`xat-rag-core` depends only on numpy and `langchain-core` (see [ADR 0001](0001-langchain-core-as-foundation.md)), so that its rules are easy to test without ONNX Runtime or SQLite.

## Consequences

- Work on retrieval quality is fast, and the evaluation tools are in the same language.
- **Accepted risk:** we did not test packaging and signing on the three platforms before this decision. The first release task freezes, signs and notarizes the service on macOS arm64, Windows x64 and Linux x64. If this fails, we write a new ADR.
- The release package is larger and starts more slowly than a .NET package. `langchain-core` with `langsmith` and the document parsers add to the size and the start time. This cost is small next to the models and the 2 GB generator.
- `lxml` (for DOCX) and the other native libraries must be signed and notarized on macOS.

## Measured (v0.1)

- The frozen service folder has about 138 MB on macOS arm64. The models are not in the folder. The PyInstaller spec excludes the packages that the service does not use.
- The first start takes about 38 seconds on macOS, because Gatekeeper checks the unsigned binaries. Later starts take under 1 second.
- A context request for three RAGs takes about 115 ms at p95 on the fixture set.
- CI freezes the service on Ubuntu, macOS 14 and Windows. It then checks the folder for denied modules (`check-bundle`), and it smoke-tests the frozen service. The sizes and start times on Windows and Linux are not recorded yet.
- The repository has no signing or notarization step. The first release must add it.
