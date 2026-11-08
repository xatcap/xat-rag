from xat_rag_core.sanitize import sanitize_text


def test_removes_ansi_control_invisible_bidi_and_tag_characters() -> None:
    rlo, pdf, shy, tag = chr(0x202E), chr(0x202C), chr(0x00AD), chr(0xE0041)
    raw = f"\x1b[31mred\x1b[0m a{chr(0x200B)}b {rlo}evil{pdf} {tag}tag\x00\x07 soft{shy}hyphen"
    assert sanitize_text(raw) == "red ab evil tag softhyphen"


def test_keeps_tabs_and_line_breaks_and_normalizes_newlines() -> None:
    assert sanitize_text("a\tb\r\nc\rd\ne") == "a\tb\nc\nd\ne"


def test_normalizes_to_nfc() -> None:
    assert sanitize_text("é") == "é"
