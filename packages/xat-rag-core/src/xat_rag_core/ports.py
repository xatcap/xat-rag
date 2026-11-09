"""The ports of xat-rag. See "Ports and adapters" in the architecture page."""

from collections.abc import Sequence
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from xat_rag_core.model import Candidate


class TokenCounter(Protocol):
    """Counts the size of a text with the fixed size ruler of xat-rag."""

    @property
    def tokenizer_id(self) -> str:
        """The ID of the tokenizer, for example `ministral-3-3b-tokenizer@<sha12>` (from the model lock)."""
        ...

    def count(self, text: str) -> int:
        """Return the number of tokens in `text`."""
        ...


class Embedder(Protocol):
    """Turns texts into normalized vectors."""

    def embed_documents_np(self, texts: Sequence[str]) -> NDArray[np.float32]:
        """Return one row for each document text."""
        ...

    def embed_query_np(self, text: str) -> NDArray[np.float32]:
        """Return the vector of a query."""
        ...


class Retriever(Protocol):
    """Finds candidates for a query text. The rank of each candidate is in its scores."""

    def search(self, query: str) -> list[Candidate]:
        """Return the candidates, best first."""
        ...


class Reranker(Protocol):
    """Orders candidates by their relevance to a query."""

    def rerank(self, candidates: Sequence[Candidate], query: str) -> list[Candidate]:
        """Return the candidates with their rerank scores, best first."""
        ...
