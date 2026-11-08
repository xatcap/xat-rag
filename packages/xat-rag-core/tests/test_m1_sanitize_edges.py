import unicodedata

import pytest
from xat_rag_core.sanitize import sanitize_text

# Invisible and ambiguous characters are written as code points, so that the file stays readable.
ZWSP = chr(0x200B)
BOM = chr(0xFEFF)
COMBINING_ACUTE = chr(0x0301)
COMBINING_RING = chr(0x030A)
RLO = chr(0x202E)
PDF = chr(0x202C)
TAG_A = chr(0xE0041)
TAG_B = chr(0xE0042)


def test_empty_and_plain_text_are_unchanged() -> None:
    assert sanitize_text("") == ""
    assert sanitize_text("plain text, 123.") == "plain text, 123."


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("\x1b[1;31mred\x1b[0m", "red"),
        ("\x1b[2Jclear", "clear"),
        ("a\x1b]0;window title\x07b", "ab"),
        ("a\x1b]8;;http://x\x1b\\link\x1b]8;;\x1b\\b", "alinkb"),
        ("\x1bMup", "up"),
        ("end\x1b", "end"),
    ],
)
def test_ansi_sequences_are_removed(raw: str, expected: str) -> None:
    assert sanitize_text(raw) == expected


def test_no_escape_character_survives_even_for_broken_sequences() -> None:
    assert "\x1b" not in sanitize_text("\x1b[\x1b[31mm\x1b[3\x1b[1m1m\x1b]0;unterminated")


@pytest.mark.parametrize(
    "code_point",
    [
        0x00,
        0x08,
        0x0B,
        0x0C,
        0x7F,
        0x80,
        0x9B,
        0x9F,
        0x200B,
        0x200C,
        0x200D,
        0x2060,
        0x2064,
        0xFEFF,
        0x180E,
        0x00AD,
        0x200E,
        0x200F,
        0x061C,
        0x202A,
        0x202E,
        0x2066,
        0x2069,
        0xE0000,
        0xE0041,
        0xE007F,
    ],
)
def test_removed_characters(code_point: int) -> None:
    assert sanitize_text(f"a{chr(code_point)}b") == "ab"


@pytest.mark.parametrize(
    "code_point",
    [0x09, 0x0A, 0x20, 0xA0, 0xA1, 0xFF],
)
def test_kept_characters(code_point: int) -> None:
    text = f"a{chr(code_point)}b"
    assert sanitize_text(text) == text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("a\r\nb", "a\nb"), ("a\rb", "a\nb"), ("a\n\rb", "a\n\nb"), ("a\r\r\nb", "a\n\nb"), ("a\r\x00\nb", "a\nb")],
)
def test_line_endings_become_line_feeds(raw: str, expected: str) -> None:
    assert sanitize_text(raw) == expected


def test_normalizes_decomposed_text_to_nfc() -> None:
    assert sanitize_text("e" + COMBINING_ACUTE) == chr(0xE9)
    assert unicodedata.is_normalized("NFC", sanitize_text("A" + COMBINING_RING + chr(0x1100) + chr(0x1161)))


def test_invisible_character_between_combining_marks_does_not_stop_composition() -> None:
    assert sanitize_text("e" + ZWSP + COMBINING_ACUTE) == chr(0xE9)


def test_characters_are_removed_before_normalizing() -> None:
    assert sanitize_text(BOM + "A" + COMBINING_RING) == chr(0xC5)


@pytest.mark.parametrize(
    "raw",
    [
        "\x1b[3\x1b[1m1m",
        "\r\x00\n",
        "e" + ZWSP + COMBINING_ACUTE + "\x1b[0m",
        TAG_A + TAG_B + " tags",
        RLO + "abc" + PDF,
        "mixed \x1b]0;t\x07 \r\n " + ZWSP + BOM + " text",
    ],
)
def test_sanitizing_twice_gives_the_same_result(raw: str) -> None:
    once = sanitize_text(raw)
    assert sanitize_text(once) == once


def test_only_removable_text_gives_an_empty_string() -> None:
    assert sanitize_text("\x00" + ZWSP + BOM + "\x1b[0m" + TAG_A) == ""


def test_long_input_is_handled() -> None:
    raw = ("ab" + ZWSP + "c\x1b[0m\r\n") * 50_000
    assert sanitize_text(raw) == "abc\n" * 50_000


def test_emoji_and_non_latin_text_stay() -> None:
    text = chr(0x1F600) + " " + "".join(chr(code) for code in (0x65E5, 0x672C, 0x8A9E, 0x20, 0x41F, 0x440, 0x438))
    assert sanitize_text(text) == text


def test_line_separators_become_line_feeds() -> None:
    assert sanitize_text(f"a{chr(0x85)}b{chr(0x2028)}c{chr(0x2029)}d") == "a\nb\nc\nd"


def test_string_escapes_and_variation_selectors_are_removed() -> None:
    assert sanitize_text("a\x1bPq#0;secret\x1b\\b\x1b]0;title") == "ab"
    assert sanitize_text(f"x{chr(0xFE0F)}{chr(0xE0100)}y") == "xy"
