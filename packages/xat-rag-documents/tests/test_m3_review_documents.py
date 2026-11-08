import contextlib
import io
import random
import time
import zipfile
from typing import Any

import docx
import pytest
from docx.enum.style import WD_STYLE_TYPE
from fixtures_documents import make_pdf
from xat_rag_core.errors import ParseError, ParseReason
from xat_rag_core.model import RawDocument, SourceKind
from xat_rag_core.parsing import Deadline
from xat_rag_documents import DocumentLimits, DocxParser, HtmlParser, PdfParser


def raw(kind: SourceKind, data: bytes) -> RawDocument:
    return RawDocument("docs:f", "docs", kind, data)


def _save(document: object) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)  # type: ignore[attr-defined]
    return buffer.getvalue()


def test_html_deep_nesting_is_linear() -> None:
    data = ("<span>a " * 8000 + "</span>" * 8000).encode()
    start = time.perf_counter()
    HtmlParser().parse(raw(SourceKind.HTML, data), Deadline.none())
    assert time.perf_counter() - start < 3.0


def test_html_without_closing_head_keeps_the_body() -> None:
    data = b"<html><head><title>T</title><body><h1>A</h1><p>text</p></body></html>"
    parsed = HtmlParser().parse(raw(SourceKind.HTML, data), Deadline.none())
    assert parsed.source.title == "T"
    assert [s.text for s in parsed.sections if s.text] == ["text"]


def test_html_inline_spacing_and_pre() -> None:
    data = b"<p>un<b>believ</b>able <code>Foo</code>, done</p><pre>def f():\n    return 1</pre>"
    parsed = HtmlParser().parse(raw(SourceKind.HTML, data), Deadline.none())
    text = parsed.sections[0].text
    assert "unbelievable Foo, done" in text
    assert "def f():\n    return 1" in text


def test_html_element_count_is_checked_before_the_parse() -> None:
    with pytest.raises(ParseError) as error:
        HtmlParser(DocumentLimits(max_html_elements=100)).parse(
            raw(SourceKind.HTML, b"<p>x</p>" * 200), Deadline.none()
        )
    assert error.value.reason is ParseReason.LIMIT_EXCEEDED


def _docx_with(xml_body: bytes) -> bytes:
    data = _save(docx.Document())
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename == "word/document.xml":
                start = content.index(b"<w:body>") + len(b"<w:body>")
                content = content[:start] + xml_body + content[start:]
            target.writestr(item, content)
    return out.getvalue()


def test_docx_grid_span_bomb_is_refused() -> None:
    body = b'<w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2000000"/></w:tcPr><w:p/></w:tc></w:tr></w:tbl>'
    with pytest.raises(ParseError) as error:
        DocxParser().parse(raw(SourceKind.DOCX, _docx_with(body)), Deadline.none())
    assert error.value.reason is ParseReason.LIMIT_EXCEEDED


def test_docx_tracked_insertions_nested_tables_and_content_controls() -> None:
    body = (
        b'<w:p><w:ins w:id="1" w:author="x"><w:r><w:t>INSERTED</w:t></w:r></w:ins>'
        b'<w:del w:id="2" w:author="x"><w:r><w:delText>DELETED</w:delText></w:r></w:del></w:p>'
        b"<w:sdt><w:sdtContent><w:p><w:r><w:t>IN-CONTROL</w:t></w:r></w:p></w:sdtContent></w:sdt>"
        b"<w:tbl><w:tr><w:tc><w:p><w:r><w:t>outer</w:t></w:r></w:p>"
        b"<w:tbl><w:tr><w:tc><w:p><w:r><w:t>INNER</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:tc></w:tr></w:tbl>"
    )
    parsed = DocxParser().parse(raw(SourceKind.DOCX, _docx_with(body)), Deadline.none())
    text = " ".join(s.text for s in parsed.sections)
    assert "INSERTED" in text
    assert "DELETED" not in text
    assert "IN-CONTROL" in text
    assert "INNER" in text
    assert "tracked_insertions_kept" in parsed.report.notes


def test_docx_text_hidden_by_a_character_style_is_removed() -> None:
    document = docx.Document()
    styles: Any = document.styles
    style: Any = styles.add_style("Secret", WD_STYLE_TYPE.CHARACTER)
    style.font.hidden = True
    paragraph = document.add_paragraph("visible ")
    paragraph.add_run("STYLE-HIDDEN", style="Secret")
    parsed = DocxParser().parse(raw(SourceKind.DOCX, _save(document)), Deadline.none())
    text = " ".join(s.text for s in parsed.sections)
    assert "visible" in text
    assert "STYLE-HIDDEN" not in text


def test_bad_zip_entries_give_parse_errors() -> None:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
    with pytest.raises(ParseError) as error:
        DocxParser().parse(raw(SourceKind.DOCX, out.getvalue()), Deadline.none())
    assert error.value.reason is ParseReason.INVALID


def test_mutated_pdfs_never_raise_raw_errors() -> None:
    good = make_pdf(["This page has enough text to count as a text layer for xat-rag."] * 3, outline=[("A", 1)])
    generator = random.Random(7)
    for _ in range(300):
        data = bytearray(good)
        for _ in range(generator.randint(1, 20)):
            data[generator.randrange(len(data))] = generator.randrange(256)
        with contextlib.suppress(ParseError):
            PdfParser().parse(raw(SourceKind.PDF, bytes(data)), Deadline.none())
