"""The limits of the document parsers."""

from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass

from xat_rag_core.errors import ParseError, ParseReason

MIB = 1024 * 1024


@dataclass(frozen=True, slots=True)
class DocumentLimits:
    """Limits against bad or very large files."""

    max_binary_bytes: int = 32 * MIB
    max_html_bytes: int = 8 * MIB
    max_pdf_pages: int = 2000
    max_page_chars: int = 200_000
    max_document_chars: int = 20_000_000
    min_page_chars: int = 20
    max_zip_entries: int = 10_000
    max_zip_uncompressed: int = 256 * MIB
    max_docx_main_part: int = 64 * MIB
    max_zip_ratio: int = 100
    max_html_elements: int = 500_000


def check_size(data: bytes, limit: int) -> None:
    """Raise `ParseError` (`too_large`) if `data` is larger than `limit` bytes."""
    if len(data) > limit:
        raise ParseError(ParseReason.TOO_LARGE, f"The document is larger than {limit // MIB} MiB.")


def limit_exceeded(name: str) -> ParseError:
    """Return a `ParseError` (`limit_exceeded`) that names the limit."""
    return ParseError(ParseReason.LIMIT_EXCEEDED, f"The document passed the limit '{name}'.")


def parse_lock(lock: AbstractContextManager[object] | None) -> AbstractContextManager[object]:
    """Return the shared lock of binary parses, or a lock that does nothing. One binary parse at a time."""
    return lock if lock is not None else nullcontext()
