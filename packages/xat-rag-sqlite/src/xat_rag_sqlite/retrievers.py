"""Retrievers over one SQLite file: lexical (FTS5) and dense (sqlite-vec).

Each retriever gets its own query text. Ranks are 1-based and go into the scores of each candidate.
See the architecture, section 3.
"""

from dataclasses import dataclass, field

import numpy as np
from xat_rag_core.model import Candidate, Chunk, StageScores
from xat_rag_core.ports import Embedder
from xat_rag_core.query import Filter

from xat_rag_sqlite.lexical import match_expression
from xat_rag_sqlite.store import SqliteRagStore

_MAX_K = 1000


def _check_k(k: int) -> None:
    if not 1 <= k <= _MAX_K:
        raise ValueError(f"k must be from 1 to {_MAX_K}.")


def _lexical(chunks: list[Chunk]) -> list[Candidate]:
    return [Candidate(chunk, StageScores(lexical_rank=rank)) for rank, chunk in enumerate(chunks, 1)]


def _dense(chunks: list[Chunk]) -> list[Candidate]:
    return [Candidate(chunk, StageScores(dense_rank=rank)) for rank, chunk in enumerate(chunks, 1)]


@dataclass(frozen=True, slots=True)
class Fts5Retriever:
    """BM25 search on the FTS5 index, with code-aware tokens. The query is the lexical terms, joined with spaces."""

    store: SqliteRagStore
    k: int = 40
    filters: Filter = field(default_factory=Filter)

    def __post_init__(self) -> None:
        """Check the settings."""
        _check_k(self.k)

    def search(self, query: str) -> list[Candidate]:
        """Return the candidates, best first."""
        expression = match_expression(query.split())
        if expression is None:
            return []
        return _lexical(self.store.search_lexical(expression, self.k, self.filters))


@dataclass(frozen=True, slots=True)
class VecRetriever:
    """Nearest-neighbor search on sqlite-vec. The query is the dense text."""

    store: SqliteRagStore
    embedder: Embedder
    k: int = 40
    filters: Filter = field(default_factory=Filter)

    def __post_init__(self) -> None:
        """Check the settings."""
        _check_k(self.k)

    def search(self, query: str) -> list[Candidate]:
        """Return the candidates, best first."""
        if not query.strip():
            return []
        vector = np.asarray(self.embedder.embed_query_np(query), dtype=np.float32)
        return _dense(self.store.search_vector(vector, self.k, self.filters))
