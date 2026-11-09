"""The split of a section that has blocks: at blocks, list items and lines, then at sentences, words and characters.

A table that fits stays whole. A larger table splits at its rows, and each piece repeats the header. Code splits at
its lines. The size limits always win over this protection.
"""

import itertools
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from semantic_text_splitter import TextSplitter
from xat_rag_core.model import BlockKind, Section, TextBlock

LIMIT = 1000
"""The size of a full piece, in the units of the `length` function."""

SENTENCE_SHARE = 250
"""The size of the sentence groups of a too large block. Later steps choose among these split points."""

SMALL_LAST_PIECE = 100
"""The last piece of a section joins the piece before it, if it is below this size and the result fits."""


MIN_PIECE = 150
"""A topic segment below this size joins the segment before it, if the result fits."""

MIN_SEMANTIC_ATOMS = 4
"""With fewer atoms than this, the split uses only the structure."""

DEPTH_FACTOR = 0.5
"""A gap is a topic change if its depth is at least the mean depth plus this share of the standard deviation."""

_WINDOW = 2

type EmbedAtoms = Callable[[Sequence[str]], NDArray[np.float32]]


@dataclass(frozen=True, slots=True)
class Piece:
    """A piece of a section. `text` is not always a slice, because a table piece repeats its header."""

    start: int
    end: int
    text: str
    solid: bool = False


type Length = Callable[[str], int]


def _small_parts(text: str, start: int, length: Length, capacity: int) -> list[tuple[int, int]]:
    # The callback returns the size. `chunk_indices` returns character offsets.
    splitter = TextSplitter.from_callback(length, capacity)
    parts = [(start + offset, start + offset + len(part)) for offset, part in splitter.chunk_indices(text)]
    return parts or [(start, start + len(text))]


def _closed_lines(text: str, first: int, last: int) -> list[Piece]:
    return [Piece(first, last, text[first:last], solid=True)] if last > first else []


def _pack_lines(text: str, block: TextBlock, length: Length) -> list[Piece]:
    pieces: list[Piece] = []
    first = last = block.start
    size = 0
    offset = block.start
    for line in text[block.start : block.end].splitlines(keepends=True):
        end = offset + len(line.rstrip("\n"))
        line_size = length(line)
        if line_size > LIMIT:
            pieces += _closed_lines(text, first, last)
            pieces += [
                Piece(a, b, text[a:b], solid=True) for a, b in _small_parts(text[offset:end], offset, length, LIMIT)
            ]
            first = last = offset + len(line)
            size = 0
        elif size + line_size > LIMIT and last > first:
            pieces += _closed_lines(text, first, last)
            first, last, size = offset, end, line_size
        else:
            first = first if last > first else offset
            last, size = end, size + line_size
        offset += len(line)
    return [*pieces, *_closed_lines(text, first, last)]


def _table_pieces(text: str, block: TextBlock, length: Length) -> list[Piece]:
    lines = text[block.start : block.end].split("\n")
    if len(lines) < 3:
        return _pack_lines(text, block, length)
    head = "\n".join(lines[:2])
    head_size = length(head + "\n")
    if head_size * 2 > LIMIT:  # A very large header leaves no room for rows. The limits win.
        return _pack_lines(text, block, length)
    pieces: list[Piece] = []
    offset = block.start + len(head) + 1
    rows: list[tuple[int, int]] = []
    size = head_size

    def flush() -> None:
        if not rows:
            return
        body = text[rows[0][0] : rows[-1][1]]
        pieces.append(Piece(rows[0][0], rows[-1][1], f"{head}\n{body}", solid=True))
        rows.clear()

    for line in lines[2:]:
        start, end = offset, offset + len(line)
        offset = end + 1
        row_size = length(line + "\n")
        if head_size + row_size > LIMIT:
            flush()
            size = head_size
            pieces += [Piece(a, b, text[a:b], solid=True) for a, b in _small_parts(line, start, length, LIMIT)]
            continue
        if size + row_size > LIMIT:
            flush()
            size = head_size
        rows.append((start, end))
        size += row_size
    flush()
    return pieces


def _atoms(section: Section, block: TextBlock, length: Length) -> list[Piece]:
    text = section.text
    whole = text[block.start : block.end]
    if length(whole) <= LIMIT:
        return [Piece(block.start, block.end, whole, solid=block.kind in {BlockKind.TABLE, BlockKind.CODE})]
    if block.kind is BlockKind.TABLE:
        return _table_pieces(text, block, length)
    if block.kind is BlockKind.CODE:
        return _pack_lines(text, block, length)
    return [Piece(a, b, text[a:b]) for a, b in _small_parts(whole, block.start, length, SENTENCE_SHARE)]


def _with_gaps(section: Section) -> list[TextBlock]:
    """Return the blocks, and a paragraph for any text between them, so that a split loses no text."""
    blocks: list[TextBlock] = []
    offset = 0
    for block in [*section.blocks, TextBlock(BlockKind.PARAGRAPH, len(section.text), len(section.text))]:
        gap = section.text[offset : block.start]
        if gap.strip():
            start = offset + len(gap) - len(gap.lstrip())
            blocks.append(TextBlock(BlockKind.PARAGRAPH, start, start + len(gap.strip())))
        if block.end > block.start:
            blocks.append(block)
        offset = max(offset, block.end)
    return blocks


def atoms_of(section: Section, length: Length) -> list[Piece]:
    """Return the smallest parts of the section that the split can join again, in document order."""
    return list(itertools.chain.from_iterable(_atoms(section, block, length) for block in _with_gaps(section)))


def _atoms_that_fit(atoms: Sequence[Piece], text: str, length: Length) -> int:
    """Return how many atoms fit in one piece. The sum of the atom sizes can pass the limit with the text between."""
    taken = len(atoms)
    while taken > 1 and length(text[atoms[0].start : atoms[taken - 1].end]) > LIMIT:
        taken -= 1
    return taken


def pack_plain(atoms: Sequence[Piece], text: str, length: Length) -> list[Piece]:
    """Join neighbouring atoms into pieces that fit, with no small last piece check."""
    pieces: list[Piece] = []
    current: list[Piece] = []

    def close() -> None:
        while current:
            taken = _atoms_that_fit(current, text, length)
            pieces.append(
                Piece(current[0].start, current[taken - 1].end, text[current[0].start : current[taken - 1].end])
            )
            del current[:taken]

    size = 0
    for atom in atoms:
        if atom.solid:
            close()
            size = 0
            pieces.append(atom)
            continue
        atom_size = length(atom.text)
        if current and size + atom_size > LIMIT:
            close()
            size = 0
        current.append(atom)
        size += atom_size
    close()
    return pieces


def pack(atoms: Sequence[Piece], text: str, length: Length) -> list[Piece]:
    """Join neighbouring atoms into pieces that fit. Each atom is counted once, and each piece is checked once."""
    return _join_small_last(pack_plain(atoms, text, length), text, length)


def _join_small_last(pieces: list[Piece], text: str, length: Length) -> list[Piece]:
    if len(pieces) < 2 or pieces[-1].solid or pieces[-2].solid or length(pieces[-1].text) >= SMALL_LAST_PIECE:
        return pieces
    joined = text[pieces[-2].start : pieces[-1].end]
    if length(joined) > LIMIT:
        return pieces
    return [*pieces[:-2], Piece(pieces[-2].start, pieces[-1].end, joined)]


def _deepest_cut(depths: Sequence[int], first: int, last: int) -> int:
    """Return the gap with the largest depth among gaps `first..last`. A tie goes to the earliest gap."""
    return max(range(first, last + 1), key=lambda gap: (depths[gap], -gap))


def _peak(values: Sequence[int], index: int, step: int) -> int:
    best = values[index]
    position = index + step
    while 0 <= position < len(values) and values[position] >= best:
        best = values[position]
        position += step
    return best


def _gap_similarities(vectors: NDArray[np.float32]) -> list[int]:
    """Return, for each gap, the cosine of the mean vectors on its two sides, in thousandths."""
    values: list[int] = []
    for gap in range(1, len(vectors)):
        left = vectors[max(0, gap - _WINDOW) : gap].mean(axis=0)
        right = vectors[gap : gap + _WINDOW].mean(axis=0)
        norm = float(np.linalg.norm(left) * np.linalg.norm(right)) or 1.0
        values.append(round(float(np.dot(left, right)) / norm * 1000))
    return values


def _segments(
    run: Sequence[Piece], cuts: set[int], depths: Sequence[int], text: str, length: Length
) -> list[tuple[int, int]]:
    """Return the runs of atoms between cuts. A segment that is still too large is cut at its deepest gap."""
    edges = [0, *sorted(cuts), len(run)]
    pending = list(itertools.pairwise(edges))
    done: list[tuple[int, int]] = []
    while pending:
        first, end = pending.pop(0)
        if end - first == 1 or length(text[run[first].start : run[end - 1].end]) <= LIMIT:
            done.append((first, end))
            continue
        gap = _deepest_cut(depths, first, end - 2) + 1  # Gap g lies between atoms g and g + 1.
        pending[:0] = [(first, gap), (gap, end)]
    return done


def _merge_small(
    segments: list[tuple[int, int]], run: Sequence[Piece], text: str, length: Length
) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for first, end in segments:
        if merged and _joins_before(merged[-1], (first, end), run, text, length):
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((first, end))
    return merged


def _joins_before(
    before: tuple[int, int], segment: tuple[int, int], run: Sequence[Piece], text: str, length: Length
) -> bool:
    small = length(text[run[segment[0]].start : run[segment[1] - 1].end]) < MIN_PIECE
    return small and length(text[run[before[0]].start : run[segment[1] - 1].end]) <= LIMIT


def _semantic_run(run: Sequence[Piece], text: str, length: Length, embed: EmbedAtoms) -> list[Piece]:
    if len(run) < MIN_SEMANTIC_ATOMS:
        return pack_plain(run, text, length)
    similarities = _gap_similarities(embed([atom.text for atom in run]))
    depths = [
        (_peak(similarities, gap, -1) - value) + (_peak(similarities, gap, 1) - value)
        for gap, value in enumerate(similarities)
    ]
    spread = statistics.pstdev(depths)
    cuts: set[int] = set()
    if spread > 0:
        threshold = statistics.fmean(depths) + DEPTH_FACTOR * spread
        cuts = {gap + 1 for gap, depth in enumerate(depths) if depth >= threshold}
    segments = _merge_small(_segments(run, cuts, depths, text, length), run, text, length)
    return [
        Piece(run[first].start, run[end - 1].end, text[run[first].start : run[end - 1].end]) for first, end in segments
    ]


def pack_semantic(atoms: Sequence[Piece], text: str, length: Length, embed: EmbedAtoms) -> list[Piece]:
    """Join neighbouring atoms, and cut where the topic changes. Tables and code stay separate pieces."""
    pieces: list[Piece] = []
    run: list[Piece] = []
    for atom in atoms:
        if atom.solid:
            pieces += _semantic_run(run, text, length, embed)
            run = []
            pieces.append(atom)
        else:
            run.append(atom)
    pieces += _semantic_run(run, text, length, embed)
    return _join_small_last(pieces, text, length)


def with_overlap(pieces: Sequence[Piece], text: str, length: Length, share: float) -> list[Piece]:
    """Start each text piece with the end of the piece before it, cut at a sentence end, if the result fits."""
    capacity = max(1, int(LIMIT * share))
    result: list[Piece] = []
    for index, piece in enumerate(pieces):
        before = pieces[index - 1] if index else None
        if before is None or piece.solid or before.solid:
            result.append(piece)
            continue
        start = _small_parts(before.text, before.start, length, capacity)[-1][0]
        extended = text[start : piece.end]
        result.append(Piece(start, piece.end, extended) if length(extended) <= LIMIT else piece)
    return result
