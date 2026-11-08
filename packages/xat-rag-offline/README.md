# xat-rag-offline

Startup hardening for the xat-rag **service**. See [ADR 0011](../../docs/adr/0011-offline-hardening.md).

The package has no dependencies. Call `harden_process()` as the first statement of the service, before any other import. Library mode must never call it, because it changes the whole process.

```python
from pathlib import Path
from xat_rag_offline import harden_process

hardening = harden_process(Path("/path/to/data"), allowed_extensions=[sqlite_vec_path])
access_key = hardening.access_key
```

`harden_process()` does these steps:

1. It deletes all `LANGSMITH_*` and `LANGCHAIN_*` variables. It sets the four tracing variables to `false`.
2. It sets the Hugging Face offline variables, `HF_HOME` in the data folder, and `DO_NOT_TRACK=1`.
3. It reads `XatRag__AccessKey` once, and it deletes the variable from the environment. It refuses two access key variables that differ only in case.
4. It deletes the variables that end in `_API_KEY`, `_TOKEN`, `_ENDPOINT` or `_ENDPOINTS`, except the `XatRag__*` names.
5. It installs a network guard (an audit hook). The guard is defense in depth only, because native code can pass it.

In the frozen app, the PyInstaller runtime hook calls only `harden_environment`, and it keeps the access key. The entry point then calls `harden_process`, which reads the key and installs the guard.
