"""A small setup of recipes for the service tests."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MODELS = ROOT / "models"
LOCK = ROOT / "build" / "models.lock.json"
KEY = "test-access-key-0123456789"

RAG = """
[rag]
name = "{name}"
embedder = "bge-small-en-v1.5"
reranker = "ms-marco-minilm-l6-v2"

[sources.main]
kind = "{kind}"
"""

CONTEXT = """
[context]
name = "ask"

[[context.sources]]
rag = "docs"
"""


def kept_environment() -> dict[str, str]:
    """Return the variables that a child process needs. Python on Windows needs more of them."""
    names = {"PATH", "HOME", "TMPDIR", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA"}
    return {name: value for name, value in os.environ.items() if name in names}


def write_setup(folder: Path) -> dict[str, str]:
    """Write recipes and return the service environment."""
    (folder / "recipes" / "rags").mkdir(parents=True)
    (folder / "recipes" / "contexts").mkdir(parents=True)
    (folder / "recipes" / "rags" / "docs.toml").write_text(RAG.format(name="docs", kind="markdown"))
    (folder / "recipes" / "rags" / "manuals.toml").write_text(RAG.format(name="manuals", kind="pdf"))
    (folder / "recipes" / "contexts" / "ask.toml").write_text(CONTEXT)
    return {
        "XatRag__DataFolder": str(folder / "data"),
        "XatRag__RecipesFolder": str(folder / "recipes"),
        "XatRag__ModelsFolder": str(MODELS),
        "XatRag__LockFile": str(LOCK),
        "XatRag__AccessKey": KEY,
        "XatRag__Threads": "1",
    }


def multipart(documents_json: str, files: dict[str, bytes]) -> tuple[bytes, str]:
    """Return a multipart body and its content type."""
    boundary = "xatragboundary7c1e"
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="documents"\r\n'.encode()
        + b"Content-Type: application/json\r\n\r\n"
        + documents_json.encode()
        + b"\r\n"
    ]
    for name, data in files.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n".encode()
            + data
            + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"
