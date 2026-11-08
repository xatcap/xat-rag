import json

import pytest
from xat_rag_core.errors import ParseError, ParseReason
from xat_rag_core.model import LineSpan, RawDocument, SourceKind
from xat_rag_core.parsing import Deadline
from xat_rag_parsers import (
    ChunkLimits,
    DiffParser,
    JiraParser,
    MarkdownParser,
    PromptParser,
    StructuralChunker,
    TextParser,
)


def words(text: str) -> int:
    return len(text.split())


def raw(kind: SourceKind, data: str, **metadata: str) -> RawDocument:
    return RawDocument(f"{kind.value}:doc", "r", kind, data.encode(), uri="u", metadata=metadata)


MD = """Intro line.

# Handbook

Welcome.

## Deploy

### Linux

Run systemctl restart shop.

### Windows

Use the service manager.

## Deploy
Second deploy section.
"""


def test_markdown_sections_and_heading_paths() -> None:
    parsed = MarkdownParser().parse(raw(SourceKind.MARKDOWN, MD), Deadline.none())
    anchors = [s.anchor for s in parsed.sections]
    assert anchors == [
        "h",
        "h/handbook",
        "h/handbook/deploy",
        "h/handbook/deploy/linux",
        "h/handbook/deploy/windows",
        "h/handbook/deploy",
    ]
    linux = parsed.sections[3]
    assert linux.header == "Handbook > Deploy > Linux"
    assert linux.text == "Run systemctl restart shop."
    assert linux.span == LineSpan(11, 11)
    assert parsed.source.title == "Handbook"


def test_markdown_chunks_get_unique_ids_and_skip_empty_sections() -> None:
    parsed = MarkdownParser().parse(raw(SourceKind.MARKDOWN, MD), Deadline.none())
    chunks = StructuralChunker(words, words, ChunkLimits(200)).chunk(parsed, "markdown-v1")
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))
    assert "markdown:doc#h/handbook/deploy~2" in ids
    assert "markdown:doc#h/handbook/deploy" not in ids  # Empty section: only a sub-heading follows.
    assert all(c.token_count and c.token_count > 0 for c in chunks)


def test_large_text_is_split_with_spans_and_limits() -> None:
    text = "\n".join(f"line {i} " + "word " * 20 for i in range(1, 41))
    parsed = TextParser().parse(raw(SourceKind.TEXT, text), Deadline.none())
    chunks = StructuralChunker(words, lambda t: len(t) // 4, ChunkLimits(120, 200)).chunk(parsed, "text-v1")
    assert len(chunks) > 1
    for chunk in chunks:
        assert words(f"{chunk.header}\n{chunk.text}") <= 120
        assert len(f"{chunk.header}\n{chunk.text}") // 4 <= 200
        assert isinstance(chunk.span, LineSpan)
        assert text.split("\n")[chunk.span.line_start - 1].startswith(chunk.text.lstrip("\n")[:10])
    assert chunks[0].chunk_id == "text:doc#t/~s1"


def test_one_long_token_is_cut() -> None:
    parsed = TextParser().parse(raw(SourceKind.TEXT, "x" * 5000), Deadline.none())
    chunks = StructuralChunker(lambda t: len(t) // 10, words, ChunkLimits(100)).chunk(parsed, "text-v1")
    assert all(len(c.text) // 10 <= 100 for c in chunks)
    assert "".join(c.text for c in chunks) == "x" * 5000


DIFF = """diff --git a/src/OrderService.java b/src/OrderService.java
--- a/src/OrderService.java
+++ b/src/OrderService.java
@@ -10,2 +10,3 @@ public class OrderService {
   int a;
-  int b;
+  int b = 0;
+  int c;
@@ -40,1 +41,1 @@ void find() {
-  return null;
+  throw new OrderNotFoundException();
"""


def test_diff_hunks() -> None:
    parsed = DiffParser().parse(
        raw(SourceKind.DIFF, DIFF, repo="shop-api", sha="4f2a9c1d", message="Fix NPE\n\nbody"), Deadline.none()
    )
    assert [s.anchor for s in parsed.sections] == ["f/src/OrderService.java/hunk/1", "f/src/OrderService.java/hunk/2"]
    first = parsed.sections[0]
    assert first.header.startswith("shop-api@4f2a9c1d:src/OrderService.java public class OrderService {")
    assert first.header.endswith("\nFix NPE")
    assert first.span == LineSpan(10, 12)
    assert first.metadata["path_name"] == "orderservice.java"
    assert first.metadata["change"] == "modified"


def test_bad_diff_and_bad_utf8() -> None:
    with pytest.raises(ParseError) as error:
        DiffParser().parse(raw(SourceKind.DIFF, "--- a\n+++ b\n@@ -1,5 +1,5 @@\n x\n"), Deadline.none())
    assert error.value.reason is ParseReason.INVALID
    with pytest.raises(ParseError):
        TextParser().parse(RawDocument("t", "r", SourceKind.TEXT, b"\xff\xfe\x00"), Deadline.none())


ISSUE = {
    "key": "PROJ-123",
    "fields": {
        "summary": "Order   lookup fails",
        "description": {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "NPE in "},
                        {"type": "mention", "attrs": {"text": "@Jane Doe"}},
                    ],
                },
                {"type": "codeBlock", "content": [{"type": "text", "text": "at OrderService.find"}]},
            ],
        },
        "status": {"name": "Done"},
        "resolution": {"name": "Fixed"},
        "components": [{"name": "orders"}, {"name": "api"}],
        "labels": ["prod", "npe"],
        "assignee": {"displayName": "Jane Doe"},
        "comment": {
            "comments": [
                {
                    "id": "10023",
                    "author": {"displayName": "Jane Doe"},
                    "body": "Fixed in 4f2a9c1",
                    "created": "2026-11-01",
                }
            ]
        },
    },
}


def test_jira_sections_metadata_and_no_names() -> None:
    parsed = JiraParser().parse(raw(SourceKind.JIRA, json.dumps(ISSUE)), Deadline.none())
    desc, comment = parsed.sections
    assert desc.anchor == "desc"
    assert desc.header == "PROJ-123: Order lookup fails"
    assert "NPE in @user" in desc.text
    assert "Jane" not in desc.text + comment.text + desc.header + comment.header
    assert comment.anchor == "comment/10023"
    assert comment.parent_anchor == "desc"
    assert desc.metadata["components"] == "api,orders"
    assert desc.metadata["labels"] == "npe,prod"
    assert desc.metadata["resolution"] == "Fixed"
    chunks = StructuralChunker(words, words, ChunkLimits(200)).chunk(parsed, "jira-v1")
    assert chunks[1].parent_id == "jira:doc#desc"


def test_jira_refuses_bad_json() -> None:
    with pytest.raises(ParseError):
        JiraParser().parse(raw(SourceKind.JIRA, "[" * 10000), Deadline.none())
    with pytest.raises(ParseError):
        JiraParser().parse(raw(SourceKind.JIRA, "{}"), Deadline.none())


def test_prompt_is_one_chunk_and_never_split() -> None:
    parsed = PromptParser().parse(raw(SourceKind.PROMPT, "You triage logs.\nBe brief."), Deadline.none())
    chunks = StructuralChunker(words, words, ChunkLimits(50)).chunk(parsed, "prompt-v1")
    assert len(chunks) == 1
    with pytest.raises(ParseError) as error:
        StructuralChunker(words, words, ChunkLimits(3)).chunk(parsed, "prompt-v1")
    assert error.value.reason is ParseReason.LIMIT_EXCEEDED


def test_deadline() -> None:
    with pytest.raises(ParseError) as error:
        Deadline(-1).check()
    assert error.value.reason is ParseReason.PARSE_TIMEOUT
