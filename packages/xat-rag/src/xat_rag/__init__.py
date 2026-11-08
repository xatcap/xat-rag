"""xat-rag: retrieves structured information for any LLM.

from xat_rag import XatRag, Settings, ContextRequest
"""

from xat_rag.app import XatRag
from xat_rag.retrieve import ContextRequest
from xat_rag.settings import Settings
from xat_rag.types import ChunkSource, DocumentResult, RetrievalResult, RetrievedChunk, SearchHit

__all__ = [
    "ChunkSource",
    "ContextRequest",
    "DocumentResult",
    "RetrievalResult",
    "RetrievedChunk",
    "SearchHit",
    "Settings",
    "XatRag",
]
