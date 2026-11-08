# xat-rag-offline

Startup hardening for the xat-rag **service**. See [ADR 0011](../../docs/adr/0011-offline-hardening.md).

The package has no dependencies. Call `harden_process()` as the first statement of the service, before any other import. Library mode must never call it, because it changes the whole process.

```python
from pathlib import Path
from xat_rag_offline import harden_process

hardening = harden_process(Path("/path/to/data"), allowed_extensions=[sqlite_vec_path])
access_key = hardening.access_key
```

[ADR 0011](../../docs/adr/0011-offline-hardening.md) lists the steps of `harden_process()`, and how the frozen app calls it.
