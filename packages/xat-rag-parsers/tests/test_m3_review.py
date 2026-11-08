import json
from typing import Any

import pytest
from xat_rag_core.errors import ParseError, ParseReason
from xat_rag_core.ids import slug
from xat_rag_core.model import LineSpan, ParsedDocument, RawDocument, Section, SourceKind
from xat_rag_core.parsing import Deadline, source_document
from xat_rag_parsers import ChunkLimits, DiffParser, JiraParser, MarkdownParser, StructuralChunker

RLO = chr(0x202E)
TAG = chr(0xE0041)


def words(text: str) -> int:
    return len(text.split())


def raw(kind: SourceKind, data: str, **metadata: str) -> RawDocument:
    return RawDocument("d", "r", kind, data.encode(), metadata=metadata)


def test_piece_ids_never_equal_a_heading_anchor() -> None:
    long = "\n".join(f"line {i} with some words" for i in range(80))
    parsed = ParsedDocument(
        source_document(raw(SourceKind.MARKDOWN, "x")),
        (
            Section("h/a", "section", "A", long, LineSpan(1, 80)),
            Section("h/a/s/1", "section", "A > S > 1", "y", LineSpan(81, 81)),
        ),
    )
    ids = [c.chunk_id for c in StructuralChunker(words, words, ChunkLimits(60)).chunk(parsed, "v")]
    assert len(ids) == len(set(ids))
    assert "d#h/a/s/1" in ids
    assert any(chunk_id.startswith("d#h/a/~s") for chunk_id in ids)


def test_parent_of_a_split_section_is_its_first_piece() -> None:
    long = " ".join(["word"] * 400)
    parsed = ParsedDocument(
        source_document(raw(SourceKind.JIRA, "x")),
        (
            Section("desc", "description", "K", long, LineSpan(1, 1)),
            Section("comment/1", "comment", "K > comment 1", "short", LineSpan(1, 1), parent_anchor="desc"),
        ),
    )
    chunks = StructuralChunker(words, words, ChunkLimits(100)).chunk(parsed, "v")
    comment = next(c for c in chunks if c.kind == "comment")
    assert comment.parent_id == "d#desc/~s1"
    assert comment.parent_id in {c.chunk_id for c in chunks}


def test_headers_titles_and_metadata_are_sanitized() -> None:
    issue = {"key": "P-1", "fields": {"summary": f"Fix{RLO}{TAG} now", "description": "x"}}
    parsed = JiraParser().parse(raw(SourceKind.JIRA, json.dumps(issue)), Deadline.none())
    chunk = StructuralChunker(words, words, ChunkLimits(100)).chunk(parsed, "v")[0]
    assert chunk.header == "P-1: Fix now"
    assert RLO not in parsed.source.title
    diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"
    parsed = DiffParser().parse(raw(SourceKind.DIFF, diff, message=f"msg{RLO}hidden"), Deadline.none())
    header = StructuralChunker(words, words, ChunkLimits(100)).chunk(parsed, "v")[0].header
    assert RLO not in header


def test_slug_keeps_other_scripts() -> None:
    assert slug("Установка") == "установка"
    assert slug("Über die Konfiguration") == "uber-die-konfiguration"
    assert slug("a~b/c#d") == "a-b-c-d"


def test_jira_wiki_mentions_bad_comments_and_null_summary() -> None:
    fields: dict[str, Any] = {
        "summary": None,
        "description": "ask [~jdoe] and [~accountid:5b10]",
        "comment": {"comments": 5},
    }
    issue: dict[str, Any] = {"key": "P-1", "fields": fields}
    parsed = JiraParser().parse(raw(SourceKind.JIRA, json.dumps(issue)), Deadline.none())
    assert "jdoe" not in parsed.sections[0].text
    assert "@user" in parsed.sections[0].text
    assert "None" not in parsed.sections[0].text
    fields["comment"] = {"comments": [{"id": "1/../x", "body": "c"}]}
    parsed = JiraParser().parse(raw(SourceKind.JIRA, json.dumps(issue)), Deadline.none())
    assert parsed.sections[1].anchor == "comment/1"


def test_diff_with_line_separators_parses_and_non_diff_text_fails() -> None:
    diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a b\n+c\n"
    parsed = DiffParser().parse(raw(SourceKind.DIFF, diff), Deadline.none())
    assert len(parsed.sections) == 1
    with pytest.raises(ParseError) as error:
        DiffParser().parse(raw(SourceKind.DIFF, "just some text\nnot a diff\n"), Deadline.none())
    assert error.value.reason is ParseReason.INVALID


def test_diff_rename_only_is_noted() -> None:
    diff = "diff --git a/old.py b/new.py\nsimilarity index 100%\nrename from old.py\nrename to new.py\n"
    parsed = DiffParser().parse(raw(SourceKind.DIFF, diff), Deadline.none())
    assert parsed.sections == ()
    assert "file_without_hunks_skipped" in parsed.report.notes


def test_markdown_spans_skip_blank_lines() -> None:
    parsed = MarkdownParser().parse(raw(SourceKind.MARKDOWN, "\n\n\nintro\n\n# A\n\ntext\n\n\n# B\nb"), Deadline.none())
    assert parsed.sections[0].span == LineSpan(4, 4)
    assert parsed.sections[1].span == LineSpan(8, 8)


def test_markdown_ignores_headings_in_lists_and_inline_markup() -> None:
    parsed = MarkdownParser().parse(
        raw(SourceKind.MARKDOWN, "# **Bold** `code` title\n\n- item\n\n  # nested\n"), Deadline.none()
    )
    assert [s.anchor for s in parsed.sections] == ["h", "h/bold-code-title"]
