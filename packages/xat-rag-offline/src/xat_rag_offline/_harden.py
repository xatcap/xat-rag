"""The entry point for service mode: `harden_process`."""

import os
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
    guard = NetworkGuard(allowed_extensions, on_block)
    guard.install()
    return guard
