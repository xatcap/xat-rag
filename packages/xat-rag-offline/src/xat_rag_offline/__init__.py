"""Startup hardening for the xat-rag service.

This package has no dependencies, and it imports nothing from LangChain. The service calls
`harden_process` before any other import. Library mode never calls it.
"""

from xat_rag_offline._env import TRACING_VARIABLES, harden_environment
from xat_rag_offline._guard import BlockedOperationError, NetworkGuard, is_loopback_address
from xat_rag_offline._harden import harden_process

__all__ = [
    "TRACING_VARIABLES",
    "BlockedOperationError",
    "NetworkGuard",
    "harden_environment",
    "harden_process",
    "is_loopback_address",
]
