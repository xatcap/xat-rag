# 0011. Turn off tracing and telemetry at startup, and guard against network access

- Status: Accepted
- Date: 2026-11-07
- Deciders: Project maintainer

## Context

xat-rag must work offline, with no telemetry and no downloads (NFR-1, NFR-2, NFR-3). `langchain-core` ships the `langsmith` client. LangSmith tracing is off by default, but it starts when one of these variables is the exact string `true`:

- `LANGSMITH_TRACING`
- `LANGSMITH_TRACING_V2`
- `LANGCHAIN_TRACING`
- `LANGCHAIN_TRACING_V2`

langsmith reads these variables once and caches the result. The shell of an end user can contain them. langsmith also reads other variables, for example `LANGSMITH_RUNS_ENDPOINTS`, `LANGSMITH_ADDRESS` and `LANGSMITH_OTEL_ONLY`.

A test on 2026-11-07 set `LANGSMITH_TRACING=true` with a fake key. One retriever call sent a POST to `api.smith.langchain.com:443`. With a real key, chunk text leaves the machine. The same call inside `langsmith.run_helpers.tracing_context(enabled=False)` sent nothing.

Other shipped packages can also use the network: `huggingface_hub` (through `tokenizers`), requests, httpx and websockets.

xat-rag runs in two modes:

- **Service mode:** the `xat-rag-service` entry point, also in the frozen app. xat-rag owns the process.
- **Library mode:** a Python app imports `xat_rag`. The host app owns the process, and it can use LangSmith or the network for its own work.

Process-wide settings are correct in service mode, but they would change the host app in library mode.

## Decision

### 1. Service mode: harden the process

The entry point of the service calls `xat_rag_offline.harden_process()` as its first statement. In the frozen app, a PyInstaller runtime hook calls only `harden_environment`, before `main`. The hook keeps the access key in the environment. Then `main` calls `harden_process`, which reads and removes the key and installs the network guard. Library mode never calls it. The package `xat-rag-offline` has no dependencies, and it imports nothing from LangChain.

`harden_process()` does these steps, before any LangChain import:

1. It deletes each variable that starts with `LANGSMITH_` or `LANGCHAIN_`, case-insensitive. Then it sets `LANGSMITH_TRACING`, `LANGSMITH_TRACING_V2`, `LANGCHAIN_TRACING` and `LANGCHAIN_TRACING_V2` to `false`.
2. It sets these variables, and overwrites old values:

   ```
   HF_HUB_OFFLINE=1  TRANSFORMERS_OFFLINE=1  HF_HUB_DISABLE_TELEMETRY=1  HF_HUB_DISABLE_UPDATE_CHECK=1
   HF_HUB_DISABLE_IMPLICIT_TOKEN=1  HF_HOME=<data folder>/hf  DO_NOT_TRACK=1
   ```

3. It reads `XatRag__AccessKey` once and deletes it from the environment, so that child processes do not get it.
4. It deletes each variable whose name ends in `_API_KEY`, `_TOKEN`, `_ENDPOINT` or `_ENDPOINTS` (case-insensitive), except names that start with `XatRag__`. The service needs none of them. This rule also deletes unrelated variables such as `OTEL_EXPORTER_OTLP_ENDPOINT`, and the docs of the service say so.
5. It installs the network guard (section 3).

A test checks that no `langchain_core` or `langsmith` module is in `sys.modules` before `harden_process()` runs.

After the imports, the composition root of the service (in `xat-rag-service`) calls `langsmith.configure(enabled=False)`, `langchain_core.globals.set_debug(False)` and `set_verbose(False)`. Debug mode prints all inputs and outputs, so it can leak content. These calls are also process-wide, so library mode does not make them.

### 2. Both modes: turn off tracing for each call

The process settings do not cover library mode, and contextvars do not pass into worker threads by default. Thus, each use case does these steps **inside the worker thread** that runs it:

- It enters `tracing_context(enabled=False)`.
- It sets the `langchain_core` tracer context variable (`tracing_v2_callback_var`) to none for the call, so that a tracer of the host app does not get our text. This is an internal name of `langchain-core`. A test checks it at each upgrade.
- It passes `config={"callbacks": []}` to each `invoke`, and `callbacks=[]` to `compress_documents`. `OnnxEmbeddings` calls no LangChain code that can trace.

In service mode, tracing is always off. In library mode, xat-rag turns tracing off for its own calls. The host app keeps control of its own process, and xat-rag does not change its environment or its LangSmith settings.

### 3. Service mode: the network guard

An audit hook (`sys.addaudithook`) refuses these events, except for loopback:

- `socket.connect`, `socket.sendto`, `socket.sendmsg`, `socket.bind`, `socket.getaddrinfo`, `socket.gethostbyname`, `socket.gethostbyaddr` and `socket.getnameinfo`. A `sendto` or `sendmsg` with no address is allowed, because it uses a connected socket whose connect was checked. asyncio sends data this way.
- `subprocess.Popen`, `os.system`, `os.exec*`, `os.spawn*`, `os.posix_spawn`, `os.fork` and `os.forkpty`. The service starts no programs and no child processes.
- `ctypes.dlopen`.
- `sqlite3.load_extension`, except for the locked sqlite-vec file. After the load, xat-rag calls `enable_load_extension(False)`.

Loopback means these addresses: Unix socket paths, `127.0.0.0/8`, `::1` (also with a zone ID), `::ffff:127.0.0.1` and the name `localhost`. `0.0.0.0`, `::` and IPv4 text with `%` (which a resolver reads as a host name) are refused. A refused call raises an error that becomes `internal_error`. The log holds the error type and the refused event name, never the address or the data.

**This guard is defense in depth, not the main control.** It sees only Python events. Native code (ONNX Runtime, SQLite extensions, lxml) and an attacker with code execution can pass it. The main controls are these:

- The dependency rules in section 4, so that no unknown code ships.
- Tests that observe the network from outside the process (section 5).
- The operating system of the user. The docs of the service tell app developers how to add an outbound firewall rule for the service program, if their policy needs it.

### 4. Packaging

- Ship only the packages of the exact `uv.lock`. A dependency allowlist that is equal to the lock fails CI when a new transitive package appears.
- A denylist that fails CI: posthog, openai, tiktoken, nltk, torch, transformers, sentence-transformers, sentry-sdk, OpenTelemetry exporters, unstructured, SQLAlchemy, aiohttp, `cryptography`, `langchain-community`, `langchain-classic`, `langchain`.
- PyInstaller `excludes` the same packages.
- The license check of NFR-11.

### 5. Tests in CI

- `pytest-socket` with `--disable-socket` for unit tests, and `--allow-hosts=127.0.0.1,::1` for service tests.
- A test with `LANGSMITH_TRACING=true`, a fake `LANGSMITH_API_KEY` and `LANGSMITH_RUNS_ENDPOINTS` set. It runs ingestion and a context request in a network namespace with a packet capture. It checks that no packet leaves loopback. The observer is outside the process, so the test does not depend on the guard.
- A library-mode test: the host enters `tracing_v2_enabled()` with a fake tracer, then calls xat-rag. The tracer must get no runs from xat-rag.
- A test with empty `HOME`, `XDG_CACHE_HOME`, `APPDATA`, `LOCALAPPDATA` and `TMPDIR`. It checks that no file appears outside the data folder.
- A smoke test of the frozen app on each platform with the network off: `unshare -n` on Linux, `sandbox-exec` with no outbound network on macOS, and a firewall rule on Windows.

## Consequences

- An environment variable of the user cannot turn on tracing in the service.
- In library mode, the host app keeps its own LangSmith, debug and network settings. xat-rag protects only its own calls.
- The `langsmith` client still ships. These layers control it, but they do not remove it.
- Each new dependency needs a review, because the allowlist fails CI.
- Some internal names (the cached environment reader of langsmith and `tracing_v2_callback_var` in `langchain_core.tracers.context`) can change in an upgrade. Tests cover them, and a failure blocks the upgrade.

## Implementation notes (v0.1)

- `harden_process(data_folder, allowed_libraries, allowed_extensions, on_block)` needs the data folder. It sets `HF_HOME` to `<data folder>/hf`.
- `harden_environment` refuses two access key variables that differ only in case. It removes the access key from the environment, and it returns the key.
- The audit hook refuses these events, unless the address is loopback:
  - Socket connect, bind, send and name lookup events. A send with no address is allowed.
  - Process events: `subprocess.Popen`, `os.system`, `os.exec*`, `os.spawn*`, `os.posix_spawn`, `os.fork` and `os.forkpty`. These are always refused.
  - `ctypes.dlopen`. The service allows no library.
  - `sqlite3.load_extension`, except the locked sqlite-vec file.
- The PyInstaller runtime hook calls `harden_environment` before `main`, and it does not call `harden_process`. It keeps the access key in the environment. Then `main` calls `harden_process`, which reads and removes the key and installs the guard.
- In library mode, `xat_rag_core.tracing.no_tracing()` clears `tracing_v2_callback_var` and `run_collector_var`. It enters `tracing_context(enabled=False, parent=False)`.
- In service mode, the loggers of pypdf, python-docx, beautifulsoup4, uvicorn access, httpx, langsmith and onnxruntime are off.
- The CI `offline` job runs the service and library tests in a new network namespace (`sudo unshare --net`), and it brings the loopback interface up. It sets `LANGSMITH_TRACING`, `LANGSMITH_API_KEY` and `LANGSMITH_RUNS_ENDPOINTS`. The job does not capture packets yet, so the packet capture of section 5 is not met yet.
- `xat-rag-offline` imports no workspace package and no LangChain package. The import-linter contract checks this.
