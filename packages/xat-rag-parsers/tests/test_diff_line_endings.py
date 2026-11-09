"""A diff must give the same sections with LF and with CRLF line endings."""

import pytest
from xat_rag_core.model import RawDocument, Section, SourceKind
from xat_rag_core.parsing import Deadline
from xat_rag_parsers.diff import DiffParser

PLAIN = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1,2 +1,2 @@\n one\n-two\n+three\n"
GIT = "diff --git a/src/app.py b/src/app.py\nindex 83db48f..bf2a1c3 100644\n" + PLAIN


def _sections(text: str) -> tuple[Section, ...]:
    raw = RawDocument(doc_id="d", rag="r", source_kind=SourceKind.DIFF, data=text.encode())
    return DiffParser().parse(raw, Deadline.none()).sections


@pytest.mark.parametrize("diff", [PLAIN, GIT], ids=["plain", "git"])
def test_crlf_gives_the_same_sections_as_lf(diff: str) -> None:
    expected = _sections(diff)
    actual = _sections(diff.replace("\n", "\r\n"))
    assert actual == expected
    section = actual[0]
    assert section.anchor == "f/src/app.py/hunk/1"
    assert section.header == "src/app.py"
    assert section.metadata["path"] == "src/app.py"
    assert section.metadata["change"] == "modified"
