import io
import itertools
import threading
import zipfile
from pathlib import Path

import pytest
from fixtures_documents import HTML, make_docx, make_layout_pdf, make_pdf, make_xlsx
from xat_rag_core.errors import ConfigError, ParseError, ParseReason
from xat_rag_core.model import BlockSpan, CellSpan, PageSpan, RawDocument, SourceKind
from xat_rag_core.parsing import Deadline
from xat_rag_documents import DocumentLimits, DocxParser, HtmlParser, PdfParser, XlsxParser
from xat_rag_parsers import ChunkLimits, StructuralChunker

MODELS = Path(__file__).resolve().parents[3] / "models"
needs_models = pytest.mark.skipif(
    not (MODELS / "docling-project--docling-layout-heron" / "model.safetensors").exists(), reason="models not fetched"
)
TEXT = "This page has enough text to count as a text layer for xat-rag."


def raw(kind: SourceKind, data: bytes) -> RawDocument:
    return RawDocument("docs:file", "docs", kind, data)


def words(text: str) -> int:
    return len(text.split())


def pdf() -> PdfParser:
    return PdfParser(models=MODELS)


def reason_of(parser: PdfParser | DocxParser | HtmlParser | XlsxParser, document: RawDocument) -> ParseReason:
    with pytest.raises(ParseError) as error:
        parser.parse(document, Deadline.none())
    return error.value.reason


@needs_models
def test_pdf_sections_follow_headings_and_not_pages() -> None:
    parsed = pdf().parse(raw(SourceKind.PDF, make_layout_pdf()), Deadline.none())
    assert [s.anchor for s in parsed.sections[1:]] == ["h/payments", "h/payments/retries", "h/payments/refunds"]
    refunds = parsed.sections[3]
    assert refunds.header == "Payments > Refunds"
    assert isinstance(refunds.span, PageSpan)
    assert refunds.span.page_end > refunds.span.page_start
    assert parsed.report.pages_total == parsed.report.pages_indexed
    everything = " ".join(s.text for s in parsed.sections)
    assert "Acme Handbook" not in everything
    assert "Internal page" not in everything
    assert "| E1 | Declined |" in everything


@needs_models
def test_pdf_pieces_have_the_pages_of_their_own_text() -> None:
    parsed = pdf().parse(raw(SourceKind.PDF, make_layout_pdf()), Deadline.none())
    chunks = StructuralChunker(words, words, ChunkLimits(60)).chunk(parsed, "v")
    spans = [c.span for c in chunks if "refunds" in c.chunk_id and isinstance(c.span, PageSpan)]
    assert spans[0].page_start < spans[-1].page_end
    assert all(a.page_end <= b.page_end for a, b in itertools.pairwise(spans))


@needs_models
def test_pdf_with_no_text_layer_fails() -> None:
    assert reason_of(pdf(), raw(SourceKind.PDF, make_pdf(["", ""]))) is ParseReason.NO_TEXT_LAYER


@needs_models
def test_pdf_with_a_password_is_refused_and_an_empty_password_is_read() -> None:
    assert reason_of(pdf(), raw(SourceKind.PDF, make_layout_pdf(password="pw"))) is ParseReason.ENCRYPTED
    parsed = pdf().parse(raw(SourceKind.PDF, make_layout_pdf(password="")), Deadline.none())
    assert any("Retries happen" in s.text for s in parsed.sections)


@needs_models
def test_pdf_invalid_and_limits() -> None:
    assert reason_of(pdf(), raw(SourceKind.PDF, b"not a pdf")) is ParseReason.INVALID
    assert (
        reason_of(
            PdfParser(DocumentLimits(max_pdf_pages=1), models=MODELS), raw(SourceKind.PDF, make_pdf([TEXT, TEXT]))
        )
        is ParseReason.LIMIT_EXCEEDED
    )
    assert (
        reason_of(PdfParser(DocumentLimits(max_binary_bytes=10), models=MODELS), raw(SourceKind.PDF, make_pdf([TEXT])))
        is ParseReason.TOO_LARGE
    )
    with pytest.raises(ParseError) as error:
        pdf().parse(raw(SourceKind.PDF, make_pdf([TEXT, TEXT])), Deadline(-1))
    assert error.value.reason is ParseReason.PARSE_TIMEOUT


def test_pdf_page_count_refuses_bad_files_before_the_models_are_used(tmp_path: Path) -> None:
    # The models folder is empty. Each file fails at the page count, before Docling loads a model.
    def parser(pages: int = 2000) -> PdfParser:
        return PdfParser(DocumentLimits(max_pdf_pages=pages), models=tmp_path)

    assert reason_of(parser(), raw(SourceKind.PDF, make_layout_pdf(password="pw"))) is ParseReason.ENCRYPTED
    assert reason_of(parser(), raw(SourceKind.PDF, b"not a pdf")) is ParseReason.INVALID
    assert reason_of(parser(), raw(SourceKind.PDF, b"%PDF-1.4 /Encrypt junk")) is ParseReason.INVALID
    two_pages = raw(SourceKind.PDF, make_pdf([TEXT, TEXT]))
    assert reason_of(parser(1), two_pages) is ParseReason.LIMIT_EXCEEDED
    empty_password = raw(SourceKind.PDF, make_layout_pdf(password=""))
    assert reason_of(parser(0), empty_password) is ParseReason.LIMIT_EXCEEDED


def test_pdf_needs_the_models_folder() -> None:
    with pytest.raises(ConfigError):
        PdfParser().parse(raw(SourceKind.PDF, make_pdf([TEXT])), Deadline.none())


def test_docx_headings_tables_and_hidden_text() -> None:
    parsed = DocxParser().parse(raw(SourceKind.DOCX, make_docx()), Deadline.none())
    anchors = [s.anchor for s in parsed.sections]
    assert anchors == ["h", "h/requirements", "h/requirements/security"]
    security = parsed.sections[2]
    assert "SECRET" not in "".join(s.text for s in parsed.sections)
    assert "| port | 443 |" in security.text
    assert isinstance(security.span, BlockSpan)


def test_docx_macros_bad_zip_and_zip_bomb() -> None:
    assert reason_of(DocxParser(), raw(SourceKind.DOCX, make_docx(macro=True))) is ParseReason.UNSUPPORTED_MEDIA_TYPE
    assert reason_of(DocxParser(), raw(SourceKind.DOCX, b"PK not a zip")) is ParseReason.INVALID
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"\x00" * 20_000_000)
    assert reason_of(DocxParser(), raw(SourceKind.DOCX, bomb.getvalue())) is ParseReason.LIMIT_EXCEEDED


def test_html_sections_removed_content_and_lists() -> None:
    parsed = HtmlParser().parse(raw(SourceKind.HTML, HTML), Deadline.none())
    assert [s.anchor for s in parsed.sections] == ["h", "h/setup", "h/setup/proxy"]
    proxy = parsed.sections[2]
    assert "Set HTTPS_PROXY first." in proxy.text or "HTTPS_PROXY" in proxy.text
    assert "- one\n\n- two" in proxy.text
    everything = " ".join(s.text for s in parsed.sections)
    for removed in ("alert", "IGNORE PREVIOUS", "hidden text", "a comment", "body{}"):
        assert removed not in everything
    assert isinstance(proxy.span, BlockSpan)


def test_html_limits() -> None:
    assert (
        reason_of(HtmlParser(DocumentLimits(max_html_elements=3)), raw(SourceKind.HTML, HTML))
        is ParseReason.LIMIT_EXCEEDED
    )
    assert reason_of(HtmlParser(DocumentLimits(max_html_bytes=10)), raw(SourceKind.HTML, HTML)) is ParseReason.TOO_LARGE


def test_xlsx_has_one_section_for_each_sheet_with_row_spans() -> None:
    parsed = XlsxParser().parse(raw(SourceKind.XLSX, make_xlsx()), Deadline.none())
    assert [s.anchor for s in parsed.sections] == ["h/rates", "h/other"]
    assert parsed.sections[0].span == CellSpan("Rates", 1, 3)
    assert "| A | 1 |" in parsed.sections[0].text


def test_shared_lock_is_used() -> None:
    lock = threading.Lock()
    lock.acquire()
    result: list[str] = []
    thread = threading.Thread(
        target=lambda: result.append(
            HtmlParser(lock=lock).parse(raw(SourceKind.HTML, HTML), Deadline.none()).sections[1].text
        )
    )
    thread.start()
    thread.join(0.2)
    assert result == []
    lock.release()
    thread.join(30)
    assert result
