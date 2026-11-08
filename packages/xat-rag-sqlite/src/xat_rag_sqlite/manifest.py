"""The index manifest. xat-rag refuses an index that does not match the loaded models and recipe."""

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, cast

from xat_rag_core.errors import IndexMismatchError, InternalError

INDEX_FORMAT = 1


@dataclass(frozen=True, slots=True)
class EmbedderInfo:
    """The embedder of an index."""

    model_id: str
    sha256: str
    dims: int
    normalize: bool = True
    query_prefix: str = ""
    document_prefix: str = ""


def _empty_versions() -> dict[str, str]:
    return {}


@dataclass(frozen=True, slots=True)
class IndexManifest:
    """The facts that decide if an index can be used. See the architecture, section 4."""

    rag: str
    embedder: EmbedderInfo
    lexical_tokenizer: str
    size_tokenizer_id: str
    index_hash: str
    chunkers: Mapping[str, str] = field(default_factory=_empty_versions)
    index_format: int = INDEX_FORMAT
    xat_rag_version: str = "0.1.0"

    def to_json(self) -> str:
        """Return the manifest as JSON."""
        data = asdict(self)
        data["chunkers"] = dict(self.chunkers)
        return json.dumps(data, sort_keys=True)

    @staticmethod
    def from_json(text: str) -> "IndexManifest":
        """Read a manifest from JSON. Raise `InternalError` if it is damaged."""
        try:
            data = cast(dict[str, Any], json.loads(text))
            embedder = EmbedderInfo(**cast(dict[str, Any], data.pop("embedder")))
            chunkers = cast(dict[str, str], data.pop("chunkers"))
            if "generator_tokenizer_id" in data:  # A manifest is written once, so old files keep the old key.
                data["size_tokenizer_id"] = data.pop("generator_tokenizer_id")
            return IndexManifest(embedder=embedder, chunkers=chunkers, **data)
        except (ValueError, TypeError, KeyError) as error:
            raise InternalError("The index manifest is damaged. Rebuild the RAG.") from error


def check_manifest(stored: IndexManifest, expected: IndexManifest) -> None:
    """Raise `IndexMismatchError` if `stored` does not match `expected`. The xat-rag version can differ."""
    differences = [
        name
        for name in (
            "rag",
            "index_format",
            "embedder",
            "lexical_tokenizer",
            "size_tokenizer_id",
            "index_hash",
            "chunkers",
        )
        if _value(stored, name) != _value(expected, name)
    ]
    if differences:
        raise IndexMismatchError(
            f"The index of the RAG '{expected.rag}' does not match the loaded models or recipe "
            f"(different: {', '.join(differences)}). Rebuild the RAG."
        )


def _value(manifest: IndexManifest, name: str) -> object:
    value: object = getattr(manifest, name)
    return dict(cast(Mapping[str, str], value)) if name == "chunkers" else value
