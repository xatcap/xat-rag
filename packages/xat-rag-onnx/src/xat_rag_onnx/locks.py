"""The model lock (`build/models.lock.json`) and the startup check of model files."""

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from xat_rag_core.errors import ConfigError

_CHUNK = 1 << 20


@dataclass(frozen=True, slots=True)
class ModelFile:
    """One locked file of a model."""

    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ModelLock:
    """The lock entry of one model."""

    id: str
    role: str
    repo: str
    revision: str
    license: str
    files: tuple[ModelFile, ...]

    def file(self, path: str) -> ModelFile:
        """Return the locked file with this path. Raise `ConfigError` if it is not locked."""
        for entry in self.files:
            if entry.path == path:
                return entry
        raise ConfigError(f"The model '{self.id}' has no locked file '{path}'.")


def load_lock(path: Path) -> dict[str, ModelLock]:
    """Read the model lock. Raise `ConfigError` if it is missing or damaged."""
    try:
        data = cast(dict[str, Any], json.loads(path.read_text("utf-8")))
        result: dict[str, ModelLock] = {}
        for model in cast(list[dict[str, Any]], data["models"]):
            files = tuple(ModelFile(f["path"], int(f["size"]), str(f["sha256"])) for f in model["files"])
            entry = ModelLock(model["id"], model["role"], model["repo"], model["revision"], model["license"], files)
            result[entry.id] = entry
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ConfigError(f"The model lock '{path}' is missing or damaged.") from error
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(_CHUNK):
            digest.update(block)
    return digest.hexdigest()


def verify_model(
    model_dir: Path, lock: ModelLock, cache_file: Path | None = None, only: frozenset[str] | None = None
) -> None:
    """Check the size and sha256 of each locked file in `model_dir`. Raise `ConfigError` on a mismatch.

    `cache_file` keeps the result for each file, keyed by path, size, modification time and file ID,
    so that later starts do not hash the files again. A program that can write to the data folder
    can change the cache, so the app must protect the data folder (architecture, section 14).
    `only` limits the check to the files that the runtime uses.
    """
    cache: dict[str, dict[str, Any]] = {}
    if cache_file is not None and cache_file.exists():
        try:
            cache = cast(dict[str, dict[str, Any]], json.loads(cache_file.read_text("utf-8")))
        except (OSError, ValueError):
            cache = {}
    changed = False
    for entry in lock.files:
        if only is not None and entry.path not in only:
            continue
        path = model_dir / entry.path
        try:
            stat = path.stat()
        except OSError as error:
            raise ConfigError(f"The model file '{path}' is missing. Run the release fetch of the models.") from error
        if stat.st_size != entry.size:
            raise ConfigError(f"The model file '{path}' has the wrong size. Get the locked file again.")
        key = str(path.resolve())
        stamp = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "ino": stat.st_ino, "sha256": entry.sha256}
        if cache.get(key) == stamp:
            continue
        if _sha256(path) != entry.sha256:
            raise ConfigError(f"The model file '{path}' does not match its locked sha256. Get the locked file again.")
        cache[key] = stamp
        changed = True
    if changed and cache_file is not None:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(cache, sort_keys=True), "utf-8")
        os.replace(temporary, cache_file)
