"""PyInstaller runtime hook: harden the environment before the service code runs.

The hook changes only the environment. The entry point then calls `harden_process()`, which
installs the network guard.
"""

import os
from pathlib import Path

from xat_rag_offline import harden_environment

_data = next((v for k, v in os.environ.items() if k.upper() == "XATRAG__DATAFOLDER"), None)
harden_environment(os.environ, Path(_data) / "hf" if _data else None)
