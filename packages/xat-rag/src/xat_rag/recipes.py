"""RAG recipes and context recipes (TOML). Unknown keys are errors. See the architecture, section 10."""

import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from xat_rag_core.errors import ConfigError

_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
SourceKindName = Literal["markdown", "text", "diff", "jira", "prompt", "pdf", "docx", "html"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _check_name(value: str) -> str:
    if not _NAME.fullmatch(value):
        raise ValueError("A name must have lowercase letters, digits and '-', and start with a letter or digit.")
    return value


class RagSection(_Strict):
    """The `[rag]` table."""

    name: str
    format: Literal[1] = 1
    embedder: str
    reranker: str = "none"
    generator: str = "ministral-3-3b"
    """The generator whose tokenizer counts `max_chunk_tokens`."""

    _name = field_validator("name")(_check_name)


class SourceRecipe(_Strict):
    """One source of a RAG, for example `[sources.jira]`."""

    kind: SourceKindName
    max_chunk_tokens: int = Field(default=350, ge=32, le=2000)
    min_page_chars: int = Field(default=20, ge=0, le=10_000)


class RetrievalRecipe(_Strict):
    """The `[retrieval]` table. These values do not change the index."""

    lexical_k: int = Field(default=40, ge=1, le=1000)
    dense_k: int = Field(default=40, ge=1, le=1000)
    exact_k: int = Field(default=20, ge=1, le=1000)
    fusion: Literal["rrf"] = "rrf"
    rrf_k: int = Field(default=60, ge=1, le=1000)
    rerank_candidates: int = Field(default=40, ge=1, le=500)
    keep: int = Field(default=8, ge=1, le=100)
    mmr_lambda: float = Field(default=0.7, ge=0.0, le=1.0)


class RagRecipe(_Strict):
    """A RAG recipe: one index and its sources."""

    rag: RagSection
    sources: dict[str, SourceRecipe]
    retrieval: RetrievalRecipe = RetrievalRecipe()

    def index_hash(self) -> str:
        """Return the hash of the fields that change the index. Retrieval fields are not in it."""
        payload = {
            "embedder": self.rag.embedder,
            "sources": {name: source.model_dump() for name, source in sorted(self.sources.items())},
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def source_for(self, kind: str) -> SourceRecipe:
        """Return the source of a kind. Raise `ConfigError` if the RAG has no source of this kind."""
        for source in self.sources.values():
            if source.kind == kind:
                return source
        raise ConfigError(f"The RAG '{self.rag.name}' has no source of the kind '{kind}'.")


class ContextSource(_Strict):
    """One RAG of a context, with its quota."""

    rag: str
    min_tokens: int = Field(default=0, ge=0)
    max_share: float = Field(default=1.0, gt=0.0, le=1.0)


class ContextSection(_Strict):
    """The `[context]` table."""

    name: str
    format: Literal[1] = 1
    generator: str
    window: int = Field(default=8192, ge=256)
    reserved_output: int = Field(default=768, ge=1)
    input_max_tokens: int = Field(default=1200, ge=16)
    pinned: list[str] = Field(default_factory=list[str])
    order: Literal["edges"] = "edges"
    citation: Literal["json-gbnf"] = "json-gbnf"
    query_builder: Literal["log-v1", "text-v1"] = "text-v1"
    sources: list[ContextSource]

    _name = field_validator("name")(_check_name)


class Limits(_Strict):
    """Stage time limits of a context request, in milliseconds."""

    query_build: int = Field(default=200, ge=1)
    retrieve: int = Field(default=500, ge=1)
    embed: int = Field(default=1000, ge=1)
    rerank: int = Field(default=2500, ge=1)
    pack: int = Field(default=300, ge=1)


class ContextRecipe(_Strict):
    """A context recipe: RAGs, a budget and a query builder."""

    context: ContextSection
    limits: Limits = Limits()


def _read[T: BaseModel](path: Path, model: type[T]) -> T:
    try:
        return model.model_validate(tomllib.loads(path.read_text("utf-8")))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"The recipe '{path.name}' could not be read.") from error
    except ValidationError as error:
        fields = ", ".join(".".join(str(part) for part in item["loc"]) for item in error.errors())
        raise ConfigError(f"The recipe '{path.name}' is not valid. Check these fields: {fields}.") from error


def load_recipes(folder: Path) -> tuple[dict[str, RagRecipe], dict[str, ContextRecipe]]:
    """Read `rags/*.toml` and `contexts/*.toml` from a folder. The file name must match the recipe name."""
    rags: dict[str, RagRecipe] = {}
    for path in sorted((folder / "rags").glob("*.toml")):
        recipe = _read(path, RagRecipe)
        if recipe.rag.name != path.stem:
            raise ConfigError(f"The RAG recipe '{path.name}' must have the name '{path.stem}'.")
        rags[recipe.rag.name] = recipe
    contexts: dict[str, ContextRecipe] = {}
    for path in sorted((folder / "contexts").glob("*.toml")):
        recipe = _read(path, ContextRecipe)
        if recipe.context.name != path.stem:
            raise ConfigError(f"The context recipe '{path.name}' must have the name '{path.stem}'.")
        unknown = [source.rag for source in recipe.context.sources if source.rag not in rags]
        if unknown:
            raise ConfigError(f"The context '{recipe.context.name}' names unknown RAGs: {', '.join(unknown)}.")
        rerankers = {rags[source.rag].rag.reranker for source in recipe.context.sources}
        if len(rerankers) > 1:
            raise ConfigError(f"All RAGs of the context '{recipe.context.name}' must use the same reranker.")
        contexts[recipe.context.name] = recipe
    return rags, contexts
