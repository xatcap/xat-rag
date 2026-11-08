"""PyInstaller runtime hook: harden the environment before the service code runs (ADR 0011).

The hook changes only the environment. The entry point then calls `harden_process()`, which reads
and removes the access key and installs the network guard. The hook keeps the access key for it.
"""

import os
import sys
from pathlib import Path

from xat_rag_offline import DuplicateAccessKeyError, harden_environment

_data = next((v for k, v in os.environ.items() if k.upper() == "XATRAG__DATAFOLDER"), None)
_key = next(((k, v) for k, v in os.environ.items() if k.upper() == "XATRAG__ACCESSKEY"), None)
try:
    harden_environment(os.environ, Path(_data) / "hf" if _data else None)
except DuplicateAccessKeyError as _error:
    sys.exit(f"xat-rag: {_error}")
if _key is not None:
    os.environ[_key[0]] = _key[1]
