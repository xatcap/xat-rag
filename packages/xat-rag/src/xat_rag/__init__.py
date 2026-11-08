"""xat-rag: RAG for small local models and small context windows.

from xat_rag import XatRag, Settings, ContextRequest
"""

from xat_rag.app import XatRag
from xat_rag.compose import ContextRequest
from xat_rag.settings import Settings
from xat_rag.types import ContextPack, DocumentResult

__all__ = ["ContextPack", "ContextRequest", "DocumentResult", "Settings", "XatRag"]
