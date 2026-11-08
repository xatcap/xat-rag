"""Get the model files of xat-rag at build time, and check them against `build/models.lock.json`.

This is a build tool. It is never shipped, and xat-rag never downloads at runtime (ADR 0004).

    uv run python tools/fetch_models.py                 # get and check all files
    uv run python tools/fetch_models.py --write-lock    # get the files of build/models.sources.json and write the lock
"""

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "build" / "models.sources.json"
LOCK = ROOT / "build" / "models.lock.json"
MODELS = ROOT / "models"
CHUNK = 1 << 20


def _url(repo: str, revision: str, path: str) -> str:
    return f"https://huggingface.co/{repo}/resolve/{revision}/{path}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, target: Path, expected: dict[str, Any] | None) -> tuple[int, str]:
    """Download to a temporary file, check it, and only then move it into place. Return its size and sha256."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "xat-rag-build"})  # noqa: S310 - fixed https URL.
    with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as handle:  # noqa: S310
        while block := response.read(CHUNK):
            handle.write(block)
    size, digest = partial.stat().st_size, _sha256(partial)
    if expected is not None and (size != expected["size"] or digest != expected["sha256"]):
        partial.unlink()
        sys.exit(f"The download of {target.name} does not match the lock (size or sha256). Stop.")
    partial.replace(target)
    return size, digest


def _fetch(model: dict[str, Any], files: list[dict[str, Any]], check: bool) -> list[dict[str, Any]]:
    """Get each file. With `check`, a local file that matches the lock is kept. Without it, all files are new."""
    results: list[dict[str, Any]] = []
    for entry in files:
        target = MODELS / model["id"] / entry["path"]
        if check and target.exists() and target.stat().st_size == entry["size"] and _sha256(target) == entry["sha256"]:
            size, digest = entry["size"], entry["sha256"]
        else:
            print(f"get {model['id']}/{entry['path']}")
            size, digest = _download(
                _url(model["repo"], model["revision"], entry["source"]), target, entry if check else None
            )
        results.append({"path": entry["path"], "source": entry["source"], "size": size, "sha256": digest})
    return results


def main() -> None:
    """Get the files, and check or write the lock."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-lock", action="store_true")
    args = parser.parse_args()
    if args.write_lock:
        sources = cast(dict[str, Any], json.loads(SOURCES.read_text()))
        models = []
        for model in sources["models"]:
            files = [{"path": f["path"], "source": f.get("source", f["path"])} for f in model["files"]]
            models.append(
                {**{k: v for k, v in model.items() if k != "files"}, "files": _fetch(model, files, check=False)}
            )
        LOCK.write_text(json.dumps({"format": 1, "models": models}, indent=2) + "\n")
        print(f"wrote {LOCK}")
        return
    lock = cast(dict[str, Any], json.loads(LOCK.read_text()))
    for model in lock["models"]:
        _fetch(model, model["files"], check=True)
    print("all model files match the lock")


if __name__ == "__main__":
    main()
