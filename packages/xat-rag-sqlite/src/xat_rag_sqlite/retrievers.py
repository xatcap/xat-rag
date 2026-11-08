"""LangChain retrievers over one SQLite file: lexical (FTS5) and dense (sqlite-vec).

Each retriever gets its own query text. The use case calls each one with `.invoke()`, inside its
own worker thread, with tracing off. Ranks are 1-based and go into the metadata through
`lc_codec`. See the architecture, section 3.
"""

from typing import override

import numpy as np
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict, Field
from xat_rag_core import lc_codec
from xat_rag_core.model import Chunk
from xat_rag_core.query import Filter

from xat_rag_sqlite.lexical import match_expression
from xat_rag_sqlite.store import SqliteRagStore


def _ranked(chunks: list[Chunk], stage: str) -> list[Document]:
    return [lc_codec.with_rank(lc_codec.chunk_to_document(chunk), stage, rank) for rank, chunk in enumerate(chunks, 1)]


class Fts5Retriever(BaseRetriever):
    """BM25 search on the FTS5 index, with code-aware tokens. The query is the lexical terms, joined with spaces."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    store: SqliteRagStore
    k: int = Field(default=40, ge=1, le=1000)
    filters: Filter = Field(default_factory=Filter)

    @override
    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        expression = match_expression(query.split())
        if expression is None:
            return []
        return _ranked(self.store.search_lexical(expression, self.k, self.filters), "lexical")


class VecRetriever(BaseRetriever):
    """Nearest-neighbor search on sqlite-vec. The query is the dense text."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    store: SqliteRagStore
    embeddings: Embeddings
    k: int = Field(default=40, ge=1, le=1000)
    filters: Filter = Field(default_factory=Filter)

    @override
    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        if not query.strip():
            return []
        vector = np.asarray(self.embeddings.embed_query(query), dtype=np.float32)
        return _ranked(self.store.search_vector(vector, self.k, self.filters), "dense")
