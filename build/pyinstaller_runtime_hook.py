"""PyInstaller runtime hook: set and harden the environment before the service code runs.

The hook changes only the environment. On Windows, it also sets the OpenBLAS thread count. The entry point
then calls `harden_process()`, which installs the network guard.
"""

import os
import sys
from pathlib import Path

from xat_rag_offline import harden_environment

if sys.platform == "win32":
    # The first PDF file loads the OpenBLAS DLL of SciPy in a job thread. On Windows, that load stopped and the job
    # never ended. OpenBLAS starts its worker threads during the DLL load. Windows holds a lock during a DLL load, and
    # a new thread cannot start until the load ends. Thus the load waits for its own threads. With one thread,
    # OpenBLAS starts no worker threads. Torch and ONNX Runtime start their own threads, so the models stay fast.
    # The value is not a default, because a larger value from the user starts the worker threads again.
    os.environ["OPENBLAS_NUM_THREADS"] = "1"

_data = next((v for k, v in os.environ.items() if k.upper() == "XATRAG__DATAFOLDER"), None)
harden_environment(os.environ, Path(_data) / "hf" if _data else None)
