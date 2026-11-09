from xat_rag_parsers.recursive_split import recursive_split


def test_pieces_keep_the_separator_at_their_start_and_their_offsets() -> None:
    text = "one two\n\nthree four\nfive"
    pieces = recursive_split(text, len, 8)
    assert pieces == [(0, "one two"), (7, "\n"), (8, "\nthree"), (14, " four"), (19, "\nfive")]
    assert all(text[start : start + len(piece)] == piece for start, piece in pieces)


def test_a_long_word_is_cut_at_characters() -> None:
    assert recursive_split("abcde", len, 2) == [(0, "ab"), (2, "cd"), (4, "e")]


def test_text_that_fits_stays_whole() -> None:
    assert recursive_split("short text", len, 100) == [(0, "short text")]


def test_a_ruler_that_counts_an_empty_text_adds_that_size_between_pieces() -> None:
    def ruler(text: str) -> int:
        return len(text) + 1

    assert recursive_split("ab cd", ruler, 6) == [(0, "ab"), (2, " cd")]
