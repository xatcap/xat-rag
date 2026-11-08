"""Load a test corpus from a folder: `<rag>/<file>` and an optional `<file>.meta.json` next to it."""

import json
from pathlib import Path
from typing import Any, cast

from xat_rag.index import source_kind
from xat_rag_core.errors import ConfigError
from xat_rag_core.model import RawDocument, Scalar

_KINDS = {
    ".md": "markdown",
    ".txt": "text",
    ".diff": "diff",
    ".json": "jira",
    ".pdf": "pdf",
    ".docx": "docx",
    ".html": "html",
}


def load_corpus(folder: Path) -> list[RawDocument]:
    """Return the documents of the corpus. The first folder level is the RAG name. The doc ID is `<rag>:<path>`."""
    documents: list[RawDocument] = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.name.endswith(".meta.json"):
            continue
        parts = path.relative_to(folder).parts
        if len(parts) < 2:
            raise ConfigError(f"Put each corpus file in a folder named after its RAG: {path.name}.")
        rag = parts[0]
        relative = path.relative_to(folder / rag).as_posix()
        meta_path = path.with_name(path.name + ".meta.json")
        meta = cast(dict[str, Any], json.loads(meta_path.read_text("utf-8"))) if meta_path.exists() else {}
        kind = str(meta.pop("kind", _KINDS.get(path.suffix.lower(), "text")))
        documents.append(
            RawDocument(
                doc_id=str(meta.pop("doc_id", f"{rag}:{relative}")),
                rag=rag,
                source_kind=source_kind(kind),
                data=path.read_bytes(),
                uri=str(meta.pop("uri", relative)),
                title=str(meta.pop("title", "")),
                metadata=cast(dict[str, Scalar], meta.pop("metadata", {})),
            )
        )
    return documents
