"""A recursive text split on blank lines, lines, spaces and characters.

Diff, JIRA and prompt chunks must not change between releases, so this code keeps the exact behaviour of
`RecursiveCharacterTextSplitter` of `langchain-text-splitters` 1.1.3, with no overlap and with the separators kept
at the start of each piece.
"""

import re
from collections.abc import Callable

_SEPARATORS = ("\n\n", "\n", " ", "")


def _split_on(text: str, separator: str) -> list[str]:
    if not separator:
        return list(text)
    parts = re.split(f"({re.escape(separator)})", text)
    pieces = [parts[0]] + [parts[i] + parts[i + 1] for i in range(1, len(parts) - 1, 2)]
    return [piece for piece in pieces if piece]


def _merge(splits: list[str], length: Callable[[str], int], chunk_size: int) -> list[str]:
    join_size = length("")  # Not always 0: a ruler can count special tokens for an empty text.
    merged: list[str] = []
    current: list[str] = []
    total = 0
    for split in splits:
        size = length(split)
        if total + size + (join_size if current else 0) > chunk_size and current:
            merged.append("".join(current))
            while total > 0:  # A piece of size 0 at the end of `current` stays there.
                total -= length(current[0]) + (join_size if len(current) > 1 else 0)
                current = current[1:]
        current.append(split)
        total += size + (join_size if len(current) > 1 else 0)
    if current:
        merged.append("".join(current))
    return merged


def _split(text: str, separators: tuple[str, ...], length: Callable[[str], int], chunk_size: int) -> list[str]:
    separator = separators[-1]
    remaining: tuple[str, ...] = ()
    for index, candidate in enumerate(separators):
        if not candidate:
            separator = candidate
            break
        if candidate in text:
            separator = candidate
            remaining = separators[index + 1 :]
            break
    final: list[str] = []
    good: list[str] = []
    for piece in _split_on(text, separator):
        if length(piece) < chunk_size:
            good.append(piece)
            continue
        if good:
            final.extend(_merge(good, length, chunk_size))
            good = []
        if remaining:
            final.extend(_split(piece, remaining, length, chunk_size))
        else:
            final.append(piece)
    if good:
        final.extend(_merge(good, length, chunk_size))
    return final


def recursive_split(text: str, length: Callable[[str], int], chunk_size: int) -> list[tuple[int, str]]:
    """Return the pieces of `text` with their start offsets."""
    pieces: list[tuple[int, str]] = []
    offset = 0
    for piece in _split(text, _SEPARATORS, length, chunk_size):
        start = text.find(piece, offset)
        pieces.append((start, piece))
        offset = start + len(piece)
    return pieces
