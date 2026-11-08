"""`StructuralChunker`: sections to chunks. Too large sections are split with the LangChain splitter."""

import importlib.metadata
import math
from collections.abc import Callable
from dataclasses import dataclass, replace

from langchain_text_splitters import RecursiveCharacterTextSplitter
from xat_rag_core.errors import ParseError, ParseReason
from xat_rag_core.ids import chunk_content_hash, chunk_id, unique_ids
from xat_rag_core.model import BlockSpan, Chunk, LineSpan, PageSpan, ParsedDocument, Scalar, Section, Span
from xat_rag_core.sanitize import sanitize_metadata, sanitize_text

type TokenCount = Callable[[str], int]

SPLITTER_VERSION = importlib.metadata.version("langchain-text-splitters")
"""The version of `langchain-text-splitters`. It goes into the manifest, because it can move boundaries."""


@dataclass(frozen=True, slots=True)
class ChunkLimits:
    """The size limits of a chunk: generator tokens (`max_chunk_tokens`) and embedder tokens."""

    max_chunk_tokens: int
    max_embed_tokens: int = 510


def _sub_span(section: Section, start: int, end: int) -> Span:
    """Return the span of `section.text[start:end]`."""
    if section.units and isinstance(section.span, LineSpan):
        return LineSpan(_unit_at(section, start), _unit_at(section, max(start, end - 1)))
    match section.span:
        case LineSpan(line_start=first):
            last_char = max(start, end - 1)
            return LineSpan(first + section.text.count("\n", 0, start), first + section.text.count("\n", 0, last_char))
        case PageSpan() | BlockSpan() if not section.units:
            return section.span
        case PageSpan():
            page_start, page_end = _unit_at(section, start), _unit_at(section, max(start, end - 1))
            return PageSpan(
                page_start, page_end, section.unit_labels.get(page_start), section.unit_labels.get(page_end)
            )
        case BlockSpan():
            return BlockSpan(_unit_at(section, start), _unit_at(section, max(start, end - 1)))


def _unit_at(section: Section, offset: int) -> int:
    unit = section.units[0][1]
    for unit_offset, number in section.units:
        if unit_offset > offset:
            break
        unit = number
    return unit


class StructuralChunker:
    """Turns the sections of a parsed document into chunks with stable IDs."""

    def __init__(self, count_generator: TokenCount, count_embedder: TokenCount, limits: ChunkLimits) -> None:
        """Make a chunker. `count_generator` counts generator tokens. `count_embedder` counts embedder tokens."""
        self._count_generator = count_generator
        self._count_embedder = count_embedder
        self._limits = limits

    def _fits(self, header: str, text: str) -> bool:
        full = f"{header}\n{text}"
        return (
            self._count_generator(full) <= self._limits.max_chunk_tokens
            and self._count_embedder(full) <= self._limits.max_embed_tokens
        )

    def chunk(self, parsed: ParsedDocument, chunker_version: str) -> list[Chunk]:
        """Return the chunks of `parsed`, in document order."""
        source = parsed.source
        # IDs are unique over all sections, also empty ones, so that an ID does not change when an
        # earlier empty section gets content later.
        section_ids = unique_ids([chunk_id(source.doc_id, section.anchor) for section in parsed.sections])
        pieces: list[tuple[Section, str, str, Span]] = []
        for raw_section, section_id in zip(parsed.sections, section_ids, strict=True):
            # Headers and metadata can hold untrusted text (titles, summaries, commit messages), so they
            # are sanitized here, for every parser (ADR 0010).
            section = replace(
                raw_section, header=sanitize_text(raw_section.header), metadata=sanitize_metadata(raw_section.metadata)
            )
            if not section.text.strip():
                continue
            if self._fits(section.header, section.text):
                pieces.append((section, section_id, section.text, section.span))
                continue
            if not section.splittable:
                raise ParseError(
                    ParseReason.LIMIT_EXCEEDED,
                    f"A '{section.kind}' section is larger than max_chunk_tokens, and it cannot be split.",
                )
            for index, (start, text) in enumerate(self._split(section), start=1):
                span = _sub_span(section, start, start + len(text))
                # "~s" cannot come from a slug, so a piece ID never equals a heading anchor.
                pieces.append((section, f"{section_id}/~s{index}", text, span))
        first_piece: dict[str, str] = {}
        for section, piece_id, _, _ in pieces:
            first_piece.setdefault(chunk_id(source.doc_id, section.anchor), piece_id)

        def parent_of(section: Section) -> str | None:
            # If the parent was split, the parent ID is the ID of its first piece.
            if section.parent_anchor is None:
                return None
            parent = chunk_id(source.doc_id, section.parent_anchor)
            return first_piece.get(parent, parent)

        return [
            self._make_chunk(
                parsed,
                section,
                final_id=final_id,
                text=text,
                span=span,
                chunker_version=chunker_version,
                parent_id=parent_of(section),
            )
            for section, final_id, text, span in pieces
        ]

    def _make_chunk(
        self,
        parsed: ParsedDocument,
        section: Section,
        *,
        final_id: str,
        text: str,
        span: Span,
        chunker_version: str,
        parent_id: str | None,
    ) -> Chunk:
        source = parsed.source
        metadata: dict[str, Scalar] = {**source.metadata, **section.metadata}
        return Chunk(
            chunk_id=final_id,
            doc_id=source.doc_id,
            rag=source.rag,
            source_kind=source.source_kind,
            kind=section.kind,
            header=section.header,
            text=text,
            span=span,
            content_hash=chunk_content_hash(section.header, text),
            chunker_version=chunker_version,
            uri=source.uri,
            title=source.title,
            parent_id=parent_id,
            token_count=self._count_generator(f"{section.header}\n{text}"),
            metadata=metadata,
        )

    def _split(self, section: Section) -> list[tuple[int, str]]:
        """Split a section. Each piece fits both limits, measured with its header."""
        generator_room = self._limits.max_chunk_tokens - self._count_generator(section.header) - 1
        embedder_room = self._limits.max_embed_tokens - self._count_embedder(section.header) - 1
        if generator_room < 1 or embedder_room < 1:
            raise ParseError(ParseReason.LIMIT_EXCEEDED, "A section header alone is larger than the chunk limits.")

        def length(text: str) -> int:
            share = max(self._count_generator(text) / generator_room, self._count_embedder(text) / embedder_room)
            return math.ceil(share * 1000)

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000, chunk_overlap=0, length_function=length, add_start_index=True, strip_whitespace=False
        )
        pieces: list[tuple[int, str]] = []
        for document in splitter.create_documents([section.text]):
            text = document.page_content
            if not text.strip():
                continue
            leading = len(text) - len(text.lstrip("\n"))
            start = int(document.metadata["start_index"]) + leading
            text = text.strip("\n")
            pieces.extend(self._hard_cut(start, text, length))
        return pieces

    @staticmethod
    def _hard_cut(start: int, text: str, length: Callable[[str], int]) -> list[tuple[int, str]]:
        """Cut a piece that is still too large (for example one long token) at character positions."""
        if length(text) <= 1000:
            return [(start, text)]
        result: list[tuple[int, str]] = []
        offset = 0
        while offset < len(text):
            low, high = 1, len(text) - offset
            while low < high:
                middle = (low + high + 1) // 2
                if length(text[offset : offset + middle]) <= 1000:
                    low = middle
                else:
                    high = middle - 1
            result.append((start + offset, text[offset : offset + low]))
            offset += low
        return result
