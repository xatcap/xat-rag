"""The default paths of the frozen service.

`main` uses this module before the hardening, so it imports only the standard library (ADR 0011).
"""

import sys
from collections.abc import Mapping
from pathlib import Path


def release_folder() -> Path | None:
    """Return the folder of the frozen executable, or `None` when the service runs from source."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent


def read_setting(environ: Mapping[str, str], name: str) -> str | None:
    """Return the value of `XatRag__<name>`. The name is compared without case. An empty value is `None`."""
    wanted = f"XATRAG__{name}".upper()
    return next((value for key, value in environ.items() if key.upper() == wanted), None) or None


def resolve_data_folder(environ: Mapping[str, str], release: Path | None) -> Path | None:
    """Return the data folder: the setting, then `<release>/data`. From source, the setting is required."""
    value = read_setting(environ, "DataFolder")
    if value:
        return Path(value)
    return None if release is None else release / "data"
