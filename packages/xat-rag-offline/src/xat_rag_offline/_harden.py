"""The entry point for service mode: `harden_process`."""

import os
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from pathlib import Path

from xat_rag_offline._env import harden_environment
from xat_rag_offline._guard import NetworkGuard


@dataclass(frozen=True)
class Hardening:
    """The result of `harden_process`."""

    access_key: str | None = field(repr=False)
    """The value of `XatRag__AccessKey`, read once. The variable is no longer in the environment."""
    guard: NetworkGuard
    """The installed network guard."""


def harden_process(
    data_folder: Path,
    allowed_extensions: Collection[str] = (),
    on_block: Callable[[str], None] | None = None,
) -> Hardening:
    """Harden the process for service mode. Call this before any other import.

    Library mode must never call this function, because it changes the environment of the
    whole process and installs an audit hook that cannot be removed.
    """
    access_key = harden_environment(os.environ, data_folder / "hf")
    guard = NetworkGuard(allowed_extensions, on_block)
    guard.install()
    return Hardening(access_key=access_key, guard=guard)
