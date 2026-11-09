import re
from collections.abc import Sequence
from dataclasses import replace

import numpy as np
from numpy.typing import NDArray
from xat_rag_core.model import BlockKind, LineSpan, ParsedDocument, Section, SourceDocument, SourceKind, TextBlock
from xat_rag_parsers import ChunkLimits, StructuralChunker
from xat_rag_parsers.block_splitter import Piece, pack, pack_semantic, with_overlap


def _count(text: str) -> int:
    return len(text.split())


def _chunks(section: Section, limit: int) -> list[str]:
    source = SourceDocument("d", "r", SourceKind.MARKDOWN, "", "T", "", "h")
    chunker = StructuralChunker(_count, _count, ChunkLimits(limit, max_embed_tokens=limit))
    return [chunk.text for chunk in chunker.chunk(ParsedDocument(source, (section,)), "v")]


def _section(*blocks: tuple[BlockKind, str]) -> Section:
    text = ""
    found: list[TextBlock] = []
    for kind, body in blocks:
        text += "\n\n" if text else ""
        found.append(TextBlock(kind, len(text), len(text) + len(body)))
        text += body
    return Section("h", "section", "H", text, LineSpan(1, 1), blocks=tuple(found))


def test_a_section_that_fits_stays_whole() -> None:
    section = _section((BlockKind.PARAGRAPH, "one two"), (BlockKind.PARAGRAPH, "three"))
    assert _chunks(section, 50) == ["one two\n\nthree"]


def test_split_prefers_block_ends() -> None:
    first, second = " ".join(["a"] * 15), " ".join(["b"] * 15)
    section = _section((BlockKind.PARAGRAPH, first), (BlockKind.PARAGRAPH, second))
    assert _chunks(section, 21) == [first, second]


def test_a_table_that_fits_is_never_cut() -> None:
    table = "| a | b |\n| --- | --- |\n| 1 | 2 |"
    para = " ".join(["x"] * 25)
    assert _chunks(_section((BlockKind.PARAGRAPH, para), (BlockKind.TABLE, table)), 30) == [para, table]


def test_a_large_table_repeats_its_header_in_each_piece() -> None:
    rows = "\n".join(f"| k{i} | v{i} |" for i in range(40))
    table = "| key | value |\n| --- | --- |\n" + rows
    chunks = _chunks(_section((BlockKind.TABLE, table)), 40)
    assert len(chunks) > 1
    assert all(chunk.startswith("| key | value |\n| --- | --- |\n| k") for chunk in chunks)
    assert sum(len(re.findall(r"\| k\d", chunk)) for chunk in chunks) == 40


def test_a_large_code_block_splits_at_lines() -> None:
    code = "\n".join(f"line {i} of code" for i in range(60))
    chunks = _chunks(_section((BlockKind.CODE, code)), 40)
    assert len(chunks) > 1
    assert all(line.startswith("line") for chunk in chunks for line in chunk.split("\n"))


def test_each_piece_fits_the_limit_also_for_one_long_paragraph() -> None:
    para = ". ".join(f"Sentence {i} has a few words" for i in range(100)) + "."
    limit = 40
    assert all(_count(f"H\n{chunk}") <= limit for chunk in _chunks(_section((BlockKind.PARAGRAPH, para)), limit))


def test_a_small_last_piece_joins_the_piece_before_it() -> None:
    text = "aaaa bbbb"
    atoms = [Piece(0, 4, "aaaa"), Piece(5, 9, "bbbb")]

    def length(value: str) -> int:
        return 950 if value == "aaaa" else 50 if value == "bbbb" else 990

    assert [p.text for p in pack(atoms, text, length)] == ["aaaa bbbb"]


def test_plain_sections_keep_the_old_split() -> None:
    section = _section((BlockKind.PARAGRAPH, " ".join(["word"] * 100)))
    plain = replace(section, blocks=())
    assert _chunks(plain, 30) != []


def _topic_atoms(*topics: str) -> tuple[list[Piece], str]:
    text = ""
    atoms: list[Piece] = []
    for topic in topics:
        text += " " if text else ""
        atoms.append(Piece(len(text), len(text) + len(topic), topic))
        text += topic
    return atoms, text


def _by_first_letter(texts: Sequence[str]) -> NDArray[np.float32]:
    axes = {"a": [1.0, 0.0], "b": [0.0, 1.0]}
    return np.asarray([axes[text[0]] for text in texts], dtype=np.float32)


def test_the_cut_is_where_the_topic_changes_and_not_at_the_size_limit() -> None:
    atoms, text = _topic_atoms("a1", "a2", "a3", "b1", "b2", "b3")

    def length(value: str) -> int:
        return 300 * len(value.split())

    assert [p.text for p in pack_semantic(atoms, text, length, _by_first_letter)] == ["a1 a2 a3", "b1 b2 b3"]
    assert [p.text for p in pack(atoms, text, length)] == ["a1 a2 a3", "b1 b2 b3"]
    shifted, shifted_text = _topic_atoms("a1", "b1", "b2", "b3", "a2", "a3")
    assert [p.text for p in pack(shifted, shifted_text, length)] == ["a1 b1 b2", "b3 a2 a3"]
    assert [p.text for p in pack_semantic(shifted, shifted_text, length, _by_first_letter)] == [
        "a1",
        "b1 b2 b3",
        "a2 a3",
    ]


def test_size_limits_win_over_the_topic() -> None:
    atoms, text = _topic_atoms(*[f"a{i}" for i in range(9)])

    def length(value: str) -> int:
        return 300 * len(value.split())

    assert all(length(p.text) <= 1000 for p in pack_semantic(atoms, text, length, _by_first_letter))


def test_few_atoms_are_not_embedded() -> None:
    atoms, text = _topic_atoms("a1", "b1", "a2")

    def fail(texts: Sequence[str]) -> NDArray[np.float32]:
        raise AssertionError(texts)

    assert [p.text for p in pack_semantic(atoms, text, len, fail)] == ["a1 b1 a2"]


def test_overlap_starts_a_piece_with_the_end_of_the_piece_before_it() -> None:
    text = "First one. Second one. Third one."
    pieces = [Piece(0, 22, text[:22]), Piece(23, 33, text[23:33])]

    def length(value: str) -> int:
        return len(value) * 10

    result = with_overlap(pieces, text, length, 0.12)
    assert result[0] == pieces[0]
    assert result[1].text == "Second one. Third one."
    assert result[1].start == 11


def test_tables_get_no_overlap() -> None:
    text = "aaa bbb"
    pieces = [Piece(0, 3, "aaa", solid=True), Piece(4, 7, "bbb")]
    assert with_overlap(pieces, text, len, 0.5) == pieces


def test_text_between_blocks_is_kept_when_a_section_is_split() -> None:
    first, second = " ".join(["a"] * 15), " ".join(["b"] * 15)
    gap = "[1]: https://example.test/ref"
    text = f"{first}\n\n{gap}\n\n{second}"
    blocks = (
        TextBlock(BlockKind.PARAGRAPH, 0, len(first)),
        TextBlock(BlockKind.PARAGRAPH, len(text) - len(second), len(text)),
    )
    section = Section("h", "section", "H", text, LineSpan(1, 1), blocks=blocks)
    assert any("example.test" in chunk for chunk in _chunks(section, 21))
