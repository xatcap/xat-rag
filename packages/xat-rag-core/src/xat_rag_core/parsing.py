"""The parser contract: `SectionParser` and a deadline.

Parsers get bytes, never paths or URLs, so a parser cannot read files or the network.
"""

import time
from typing import Protocol

from xat_rag_core.errors import ParseError, ParseReason
from xat_rag_core.ids import sha256_hex
from xat_rag_core.model import ParsedDocument, RawDocument, SourceDocument
from xat_rag_core.sanitize import sanitize_metadata, sanitize_text

_BOM = "﻿"


class Deadline:
    """A time limit for one document. Parsers call `check()` between pages, blocks or sections."""

    def __init__(self, seconds: float) -> None:
        """Start the time limit now."""
        self._seconds = seconds
        self._end = time.monotonic() + seconds

    @property
    def seconds(self) -> float:
        """The length of the time limit."""
        return self._seconds

    def restart(self, seconds: float | None = None) -> None:
        """Start the time limit again, with a new length if `seconds` is given.

        Parsers call this after they get the shared parse lock, so that the time spent waiting for the
        lock does not count.
        """
        if seconds is not None:
            self._seconds = seconds
        self._end = time.monotonic() + self._seconds

    def check(self) -> None:
        """Raise `ParseError` with the reason `parse_timeout` if the time limit has passed."""
        if time.monotonic() > self._end:
            raise ParseError(ParseReason.PARSE_TIMEOUT, f"The document took longer than {self._seconds:g} s to parse.")

    @staticmethod
    def none() -> "Deadline":
        """Return a deadline that does not end, for tests and tools."""
        return Deadline(float("inf"))


class SectionParser(Protocol):
    """Parses the bytes of one document into sections."""

    @property
    def kind(self) -> str:
        """The source kind, for example `markdown`."""
        ...

    @property
    def version(self) -> str:
        """The parser and chunking rules version, for example `markdown-v1`. It goes into the manifest."""
        ...

    def parse(self, raw: RawDocument, deadline: Deadline) -> ParsedDocument:
        """Parse the document. Raise `ParseError` with a reason if it fails."""
        ...


def decode_text(data: bytes) -> str:
    """Decode UTF-8 text and remove a byte order mark. Raise `ParseError` if the bytes are not UTF-8."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ParseError(ParseReason.INVALID, "The document is not valid UTF-8 text.") from error
    return text.removeprefix(_BOM)


def source_document(raw: RawDocument, title: str | None = None) -> SourceDocument:
    """Return the `SourceDocument` of a raw document. The content hash is the hash of the raw bytes."""
    chosen = title if title is not None else raw.title
    return SourceDocument(
        doc_id=raw.doc_id,
        rag=raw.rag,
        source_kind=raw.source_kind,
        uri=raw.uri,
        title=" ".join(sanitize_text(chosen).split()),
        version=raw.version,
        content_hash=sha256_hex(raw.data),
        metadata=sanitize_metadata(raw.metadata),
    )
