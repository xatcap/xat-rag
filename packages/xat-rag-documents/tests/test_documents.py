import io
import threading
import zipfile

import pytest
from fixtures_documents import HTML, make_docx, make_pdf
from xat_rag_core.errors import ParseError, ParseReason
from xat_rag_core.model import BlockSpan, LineSpan, PageSpan, RawDocument, SourceKind
from xat_rag_core.parsing import Deadline
from xat_rag_documents import DocumentLimits, DocxParser, HtmlParser, PdfParser
from xat_rag_parsers import ChunkLimits, StructuralChunker


def raw(kind: SourceKind, data: bytes) -> RawDocument:
    return RawDocument(f"docs:file.{kind.value}", "docs", kind, data, uri="file")


def words(text: str) -> int:
    return len(text.split())


TEXT = "This page has enough text to count as a text layer for xat-rag."


def test_pdf_pages_without_outline_and_skipped_pages() -> None:
    parsed = PdfParser().parse(raw(SourceKind.PDF, make_pdf([TEXT + " one", "", TEXT + " three"])), Deadline.none())
    assert [s.anchor for s in parsed.sections] == ["p/1", "p/3"]
    assert parsed.sections[1].span == PageSpan(3, 3, "3", "3")
    assert parsed.report.pages_total == 3
    assert parsed.report.pages_indexed == 2
    assert [(p.page, p.reason) for p in parsed.report.skipped_pages] == [(2, "no_text")]


def test_pdf_outline_sections_and_split_page_spans() -> None:
    pages = [TEXT + " intro", TEXT + " install a", TEXT + " install b", TEXT + " linux"]
    data = make_pdf(pages, outline=[("Install", 1), ("Linux", 3)])
    parsed = PdfParser().parse(raw(SourceKind.PDF, data), Deadline.none())
    assert [s.anchor for s in parsed.sections] == ["h", "h/install", "h/linux"]
    install = parsed.sections[1]
    assert install.span == PageSpan(2, 3, "2", "3")
    chunks = StructuralChunker(words, words, ChunkLimits(25)).chunk(parsed, "pdf-v1")
    spans = [c.span for c in chunks if c.chunk_id.startswith("docs:file.pdf#h/install")]
    assert {(s.page_start, s.page_end) for s in spans if isinstance(s, PageSpan)} == {(2, 2), (3, 3)}


def test_pdf_with_no_text_layer_fails() -> None:
    with pytest.raises(ParseError) as error:
        PdfParser().parse(raw(SourceKind.PDF, make_pdf(["", ""])), Deadline.none())
    assert error.value.reason is ParseReason.NO_TEXT_LAYER


def test_pdf_encrypted_invalid_and_limits() -> None:
    with pytest.raises(ParseError) as error:
        PdfParser().parse(raw(SourceKind.PDF, make_pdf([TEXT], password="pw")), Deadline.none())
    assert error.value.reason is ParseReason.ENCRYPTED
    with pytest.raises(ParseError) as error:
        PdfParser().parse(raw(SourceKind.PDF, b"not a pdf"), Deadline.none())
    assert error.value.reason is ParseReason.INVALID
    with pytest.raises(ParseError) as error:
        PdfParser(DocumentLimits(max_pdf_pages=1)).parse(raw(SourceKind.PDF, make_pdf([TEXT, TEXT])), Deadline.none())
    assert error.value.reason is ParseReason.LIMIT_EXCEEDED
    with pytest.raises(ParseError) as error:
        PdfParser(DocumentLimits(max_binary_bytes=10)).parse(raw(SourceKind.PDF, make_pdf([TEXT])), Deadline.none())
    assert error.value.reason is ParseReason.TOO_LARGE
    with pytest.raises(ParseError) as error:
        PdfParser().parse(raw(SourceKind.PDF, make_pdf([TEXT, TEXT])), Deadline(-1))
    assert error.value.reason is ParseReason.PARSE_TIMEOUT


def test_docx_headings_tables_and_hidden_text() -> None:
    parsed = DocxParser().parse(raw(SourceKind.DOCX, make_docx()), Deadline.none())
    assert [s.anchor for s in parsed.sections] == ["h", "h/requirements", "h/requirements/security"]
    requirements, security = parsed.sections[1], parsed.sections[2]
    assert requirements.text == "Visible text."
    assert "SECRET" not in "".join(s.text for s in parsed.sections)
    assert "| port | 443 |" in security.text
    assert isinstance(security.span, BlockSpan)
    assert security.header == "Spec > Requirements > Security"


def test_docx_macros_bad_zip_and_zip_bomb() -> None:
    with pytest.raises(ParseError) as error:
        DocxParser().parse(raw(SourceKind.DOCX, make_docx(macro=True)), Deadline.none())
    assert error.value.reason is ParseReason.UNSUPPORTED_MEDIA_TYPE
    with pytest.raises(ParseError) as error:
        DocxParser().parse(raw(SourceKind.DOCX, b"PK not a zip"), Deadline.none())
    assert error.value.reason is ParseReason.INVALID
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"\x00" * 20_000_000)
    with pytest.raises(ParseError) as error:
        DocxParser().parse(raw(SourceKind.DOCX, bomb.getvalue()), Deadline.none())
    assert error.value.reason is ParseReason.LIMIT_EXCEEDED


def test_html_sections_removed_content_and_source_lines() -> None:
    parsed = HtmlParser().parse(raw(SourceKind.HTML, HTML), Deadline.none())
    assert parsed.source.title == "Guide"
    assert [s.anchor for s in parsed.sections] == ["h", "h/setup", "h/setup/proxy"]
    proxy = parsed.sections[2]
    assert "Set HTTPS_PROXY first." in proxy.text
    assert "one\ntwo" in proxy.text
    everything = " ".join(s.text for s in parsed.sections)
    for removed in ("alert", "IGNORE PREVIOUS", "hidden text", "a comment", "body{}"):
        assert removed not in everything
    assert proxy.span == LineSpan(8, 12)


def test_html_limits() -> None:
    with pytest.raises(ParseError) as error:
        HtmlParser(DocumentLimits(max_html_elements=3)).parse(raw(SourceKind.HTML, HTML), Deadline.none())
    assert error.value.reason is ParseReason.LIMIT_EXCEEDED
    with pytest.raises(ParseError) as error:
        HtmlParser(DocumentLimits(max_html_bytes=10)).parse(raw(SourceKind.HTML, HTML), Deadline.none())
    assert error.value.reason is ParseReason.TOO_LARGE


def test_shared_lock_is_used() -> None:
    lock = threading.Lock()
    lock.acquire()
    result: list[str] = []
    thread = threading.Thread(
        target=lambda: result.append(
            HtmlParser(lock=lock).parse(raw(SourceKind.HTML, HTML), Deadline.none()).source.title
        )
    )
    thread.start()
    thread.join(0.2)
    assert result == []
    lock.release()
    thread.join(5)
    assert result == ["Guide"]
