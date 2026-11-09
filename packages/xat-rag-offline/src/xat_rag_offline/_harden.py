"""The entry point for service mode: `harden_process`."""

import os
import platform
from collections.abc import Callable, Collection
from pathlib import Path

from xat_rag_offline._env import harden_environment
from xat_rag_offline._guard import NetworkGuard


def harden_process(
    data_folder: Path,
    allowed_extensions: Collection[str] = (),
    on_block: Callable[[str], None] | None = None,
) -> NetworkGuard:
    """Harden the process for service mode, and return the installed guard. Call this before any other import.

    Library mode must never call this function, because it changes the environment of the
    whole process and installs an audit hook that cannot be removed.
    """
    harden_environment(os.environ, data_folder / "hf")
    # `platform` starts the programs `file` and `uname` once, and keeps the result. Docling asks for it on import, and
    # the guard refuses a new process, so the result must exist before the guard starts.
    platform.platform()
    guard = NetworkGuard(allowed_extensions, on_block)
    guard.install()
    return guard
