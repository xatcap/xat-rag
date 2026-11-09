"""The paths of the service settings, and their defaults in the frozen service.

`main` uses this module before the hardening, so it imports only the standard library.
When the service runs from source, `release` is `None` and no default applies.
"""

import sys
from collections.abc import Mapping
from pathlib import Path

LOCK_FILE_NAME = "models.lock.json"
READY_FILE_NAME = "ready.json"


def release_folder() -> Path | None:
    """Return the folder of the frozen executable, or `None` when the service runs from source."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent


def read_setting(environ: Mapping[str, str], name: str) -> str | None:
    """Return the value of `XatRag__<name>`, or `None` when it is not set. The name is compared without case."""
    wanted = f"XATRAG__{name}".upper()
    return next((value for key, value in environ.items() if key.upper() == wanted), None)


def _path_setting(environ: Mapping[str, str], name: str, default: Path | None) -> Path | None:
    value = read_setting(environ, name)
    return Path(value) if value else default


def resolve_data_folder(environ: Mapping[str, str], release: Path | None) -> Path | None:
    """Return the data folder: the setting, then `<release>/data`."""
    return _path_setting(environ, "DataFolder", None if release is None else release / "data")


def resolve_recipes_folder(environ: Mapping[str, str], release: Path | None) -> Path | None:
    """Return the recipes folder: the setting, then `<release>/recipes`."""
    return _path_setting(environ, "RecipesFolder", None if release is None else release / "recipes")


def resolve_models_folder(environ: Mapping[str, str], release: Path | None) -> Path | None:
    """Return the models folder: the setting, then `<release>/models`."""
    return _path_setting(environ, "ModelsFolder", None if release is None else release / "models")


def resolve_lock_file(environ: Mapping[str, str], release: Path | None) -> Path | None:
    """Return the model lock file: the setting, then the lock file in the models folder of the release."""
    models = resolve_models_folder(environ, release) if release is not None else None
    return _path_setting(environ, "LockFile", None if models is None else models / LOCK_FILE_NAME)


def resolve_ready_file(environ: Mapping[str, str], release: Path | None) -> Path | None:
    """Return the ready file: the setting, then the ready file in the data folder of the release.

    An empty setting means no ready file.
    """
    value = read_setting(environ, "ReadyFile")
    if value is not None:
        return Path(value) if value else None
    data = resolve_data_folder(environ, release) if release is not None else None
    return None if data is None else data / READY_FILE_NAME
