"""`StructuralChunker`: sections to chunks. Too large sections are split on blank lines, lines and spaces."""

import bisect
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from itertools import chain

import numpy as np
from numpy.typing import NDArray
from xat_rag_core.errors import ParseError, ParseReason
from xat_rag_core.ids import chunk_content_hash, chunk_id, unique_ids
from xat_rag_core.model import BlockSpan, CellSpan, Chunk, LineSpan, PageSpan, ParsedDocument, Scalar, Section, Span
from xat_rag_core.parsing import Deadline
from xat_rag_core.sanitize import sanitize_metadata, sanitize_text

from xat_rag_parsers.block_splitter import Piece, atoms_of, pack, pack_semantic, with_overlap
from xat_rag_parsers.recursive_split import recursive_split

type TokenCount = Callable[[str], int]
type EmbedBatch = Callable[[Sequence[str]], NDArray[np.float32]]

MAX_BOUNDARY_UNITS = 20_000
"""A document with more parts than this to embed for split points uses only the structure. The count is exact."""

EMBED_SECONDS_PER_UNIT = 0.25
"""The time for one embedded part. The deadline for the split points is this time for each part, at least 60 s."""

_MIN_EMBED_SECONDS = 60.0
_BATCH = 32

BLOCK_CHUNKER_ID = "blocks1"
"""The version of the split rules for sections with blocks. It goes into the manifest."""

BLOCK_KINDS = frozenset({"markdown", "text", "pdf", "docx", "html", "xlsx"})
"""The source kinds that use the split at blocks."""

SPLITTER_VERSION = "1.1.3"
"""The version of the split rules. It goes into the manifest. It stays fixed, so that no RAG needs a rebuild."""


@dataclass(frozen=True, slots=True)
class ChunkLimits:
    """The size limits of a chunk: size-ruler tokens (`max_chunk_tokens`) and embedder tokens."""

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
        case PageSpan() | BlockSpan() | CellSpan() if not section.units:
            return section.span
        case PageSpan():
            page_start, page_end = _unit_at(section, start), _unit_at(section, max(start, end - 1))
            return PageSpan(
                page_start, page_end, section.unit_labels.get(page_start), section.unit_labels.get(page_end)
            )
        case BlockSpan():
            return BlockSpan(_unit_at(section, start), _unit_at(section, max(start, end - 1)))
        case CellSpan(sheet=sheet):
            return CellSpan(sheet, _unit_at(section, start), _unit_at(section, max(start, end - 1)))


def _unit_at(section: Section, offset: int) -> int:
    index = bisect.bisect_right(section.units, offset, key=lambda item: item[0])
    return section.units[max(index - 1, 0)][1]


@dataclass(frozen=True, slots=True)
class _Prepared:
    """A section with text, sanitized, and the atoms that its split uses."""

    section: Section
    section_id: str
    fits: bool
    atoms: tuple[Piece, ...] = ()

    @property
    def embedded_atoms(self) -> int:
        """The number of atoms that get an embedding for the split points. Tables and code get none."""
        return sum(not atom.solid for atom in self.atoms)


def _longest_fitting(text: str, offset: int, length: Callable[[str], int]) -> int:
    """Return the largest number of characters from `offset` that fit one piece."""
    low, high = 1, len(text) - offset
    while low < high:
        middle = (low + high + 1) // 2
        if length(text[offset : offset + middle]) <= 1000:
            low = middle
        else:
            high = middle - 1
    return low


class StructuralChunker:
    """Turns the sections of a parsed document into chunks with stable IDs."""

    def __init__(
        self,
        count_size: TokenCount,
        count_embedder: TokenCount,
        limits: ChunkLimits,
        *,
        embed_batch: EmbedBatch | None = None,
        overlap_share: float = 0.0,
    ) -> None:
        """Make a chunker. `count_size` is the size ruler. `count_embedder` counts embedder tokens.

        With `embed_batch`, a too large section is cut where the topic changes.
        """
        self._embed_batch = embed_batch
        self._overlap_share = overlap_share
        self._count_size = count_size
        self._count_embedder = count_embedder
        self._limits = limits

    def _fits(self, header: str, text: str) -> bool:
        full = f"{header}\n{text}"
        return (
            self._count_size(full) <= self._limits.max_chunk_tokens
            and self._count_embedder(full) <= self._limits.max_embed_tokens
        )

    def chunk(
        self,
        parsed: ParsedDocument,
        chunker_version: str,
        deadline: Deadline | None = None,
        notes: list[str] | None = None,
    ) -> list[Chunk]:
        """Return the chunks of `parsed`, in document order. A note about the split goes into `notes`."""
        limit = deadline or Deadline.none()
        source = parsed.source
        # IDs are unique over all sections, also empty ones, so that an ID does not change when an
        # earlier empty section gets content later.
        section_ids = unique_ids([chunk_id(source.doc_id, section.anchor) for section in parsed.sections])
        prepared = [
            item
            for raw_section, section_id in zip(parsed.sections, section_ids, strict=True)
            if (item := self._prepare(raw_section, section_id, limit)) is not None
        ]
        embed = self._boundary_embedder(prepared, notes)
        pieces = list(chain.from_iterable(self._section_pieces(item, embed) for item in prepared))
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
            token_count=self._count_size(f"{section.header}\n{text}"),
            metadata=metadata,
        )

    def _prepare(self, raw_section: Section, section_id: str, limit: Deadline) -> _Prepared | None:
        """Sanitize a section and find its atoms, once. Return `None` for a section with no text."""
        limit.check()
        # Headers and metadata can hold untrusted text (titles, summaries, commit messages), so they
        # are sanitized here, for every parser.
        section = replace(
            raw_section, header=sanitize_text(raw_section.header), metadata=sanitize_metadata(raw_section.metadata)
        )
        if not section.text.strip():
            return None
        if self._fits(section.header, section.text):
            return _Prepared(section, section_id, fits=True)
        if not section.splittable:
            raise ParseError(
                ParseReason.LIMIT_EXCEEDED,
                f"A '{section.kind}' section is larger than max_chunk_tokens, and it cannot be split.",
            )
        atoms = atoms_of(section, self._length(section)) if section.blocks else ()
        return _Prepared(section, section_id, fits=False, atoms=tuple(atoms))

    def _section_pieces(self, item: _Prepared, embed: EmbedBatch | None) -> list[tuple[Section, str, str, Span]]:
        section = item.section
        if item.fits:
            return [(section, item.section_id, section.text, section.span)]
        # "~s" cannot come from a slug, so a piece ID never equals a heading anchor.
        return [
            (section, f"{item.section_id}/~s{index}", text, _sub_span(section, start, end))
            for index, (start, end, text) in enumerate(self._split_section(item, embed), start=1)
        ]

    def _boundary_embedder(self, prepared: Sequence[_Prepared], notes: list[str] | None) -> EmbedBatch | None:
        """Return the embed function for the split points of this document, or `None` to use only the structure."""
        if self._embed_batch is None:
            return None
        embed_batch = self._embed_batch
        units = sum(item.embedded_atoms for item in prepared)
        if units > MAX_BOUNDARY_UNITS:
            if notes is not None:
                notes.append("semantic_split_unit_limit")
            return None
        deadline = Deadline(max(_MIN_EMBED_SECONDS, units * EMBED_SECONDS_PER_UNIT))

        def embed(texts: Sequence[str]) -> NDArray[np.float32]:
            rows: list[NDArray[np.float32]] = []
            for start in range(0, len(texts), _BATCH):
                deadline.check()
                rows.append(embed_batch(texts[start : start + _BATCH]))
            return np.concatenate(rows, axis=0)

        return embed

    def _split_section(self, item: _Prepared, embed: EmbedBatch | None) -> list[tuple[int, int, str]]:
        """Split a section at its blocks if it has them. Return the start, the end and the text of each piece."""
        section = item.section
        if section.blocks:
            length = self._length(section)
            found: list[Piece]
            if embed is not None:
                found = pack_semantic(item.atoms, section.text, length, embed)
            else:
                found = pack(item.atoms, section.text, length)
            if self._overlap_share > 0:
                found = with_overlap(found, section.text, length, self._overlap_share)
            return [(piece.start, piece.end, piece.text) for piece in found]
        return [(start, start + len(text), text) for start, text in self._split(section)]

    def _length(self, section: Section) -> Callable[[str], int]:
        """Return the size function of a section: 1000 is a piece that fills the room left by the header."""
        size_room = self._limits.max_chunk_tokens - self._count_size(section.header) - 1
        embedder_room = self._limits.max_embed_tokens - self._count_embedder(section.header) - 1
        if size_room < 1 or embedder_room < 1:
            raise ParseError(ParseReason.LIMIT_EXCEEDED, "A section header alone is larger than the chunk limits.")

        def length(text: str) -> int:
            share = max(self._count_size(text) / size_room, self._count_embedder(text) / embedder_room)
            return math.ceil(share * 1000)

        return length

    def _split(self, section: Section) -> list[tuple[int, str]]:
        """Split a section. Each piece fits both limits, measured with its header."""
        length = self._length(section)
        pieces: list[tuple[int, str]] = []
        for offset, piece in recursive_split(section.text, length, 1000):
            if not piece.strip():
                continue
            leading = len(piece) - len(piece.lstrip("\n"))
            start = offset + leading
            text = piece.strip("\n")
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
            size = _longest_fitting(text, offset, length)
            result.append((start + offset, text[offset : offset + size]))
            offset += size
        return result
