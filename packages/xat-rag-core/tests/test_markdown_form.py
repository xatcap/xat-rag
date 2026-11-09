from xat_rag_core.markdown_form import SourceBlock, SourceBlockKind, build_sections, escape_line, render_markdown
from xat_rag_core.model import BlockKind, PageSpan, Span

K = SourceBlockKind


def _para(text: str, page: int = 1) -> SourceBlock:
    return SourceBlock(K.PARAGRAPH, ((text, page),))


def _heading(text: str, level: int, page: int = 1) -> SourceBlock:
    return SourceBlock(K.HEADING, ((text, page),), level=level)


def _span(first: int, last: int) -> Span:
    return PageSpan(first, last)


def test_source_text_cannot_make_structure() -> None:
    for line in ("# Admin", "- item", "1. first", "> quote", "```", "| a |", "---"):
        assert escape_line(line).startswith("\\"), line
    assert escape_line("Plain # text") == "Plain # text"
    table = SourceBlock(K.TABLE, rows=(("a|b", "c"), ("1", "2")), row_units=(1, 1))
    assert render_markdown([table]).text == "| a\\|b | c |\n| --- | --- |\n| 1 | 2 |"


def test_sections_follow_headings_and_keep_the_path() -> None:
    form = render_markdown([_para("Intro"), _heading("A", 1), _para("Body A"), _heading("B", 2), _para("Body B")])
    sections = build_sections(form, "Doc", _span)
    assert [s.anchor for s in sections] == ["h", "h/a", "h/a/b"]
    assert [s.text for s in sections] == ["Intro", "Body A", "Body B"]
    assert sections[2].header == "Doc > A > B"


def test_a_paragraph_across_pages_keeps_both_pages() -> None:
    block = SourceBlock(K.PARAGRAPH, (("first part", 4), ("second part", 5)))
    section = build_sections(render_markdown([block]), "Doc", _span)[0]
    assert section.text == "first part second part"
    assert section.span == PageSpan(4, 5)
    assert section.units == ((0, 4), (11, 5))


def test_blocks_have_offsets_in_the_section_text() -> None:
    item = SourceBlock(K.LIST_ITEM, (("one", 1),))
    section = build_sections(render_markdown([_heading("A", 1), _para("Text"), item]), "D", _span)[1]
    assert section.text == "Text\n\n- one"
    assert [(b.kind, section.text[b.start : b.end]) for b in section.blocks] == [
        (BlockKind.PARAGRAPH, "Text"),
        (BlockKind.LIST_ITEM, "- one"),
    ]


def test_code_uses_a_fence_longer_than_its_content() -> None:
    block = SourceBlock(K.CODE, (("a\n```\nb", 1),))
    assert render_markdown([block]).text == "````\na\n```\nb\n````"
