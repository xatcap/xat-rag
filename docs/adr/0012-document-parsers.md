# 0012. Parse PDF, DOCX and HTML in the service process, with limits

- Status: Accepted
- Date: 2026-11-07
- Deciders: Project maintainer

## Context

v1 must ingest PDF, DOCX and HTML files. They are untrusted binary or markup files, and their parsers have had bugs that loop for a long time or use much memory. The maintainer chose to parse them in the service process, not in separate processes. v1 has no OCR. The maintainer chose to index the pages of a scanned PDF that have text.

LangChain community loaders (`PyPDFLoader`, `Docx2txtLoader` and others) take paths or URLs, and can download web paths. That breaks push-only ingestion ([ADR 0010](0010-untrusted-content-privacy-and-ingestion.md)).

## Decision

- **Our own parser for each kind** (a xat-rag `SectionParser`), on these libraries:

  | Kind | Library | License |
  |---|---|---|
  | PDF | `pypdf` | BSD-3-Clause |
  | DOCX | `python-docx`, with `lxml` | MIT, BSD-3-Clause |
  | HTML | `beautifulsoup4`, `soupsieve`, with the standard `html.parser` | MIT |

  The versions are exact pins in `uv.lock`, and the manifest records them. `cryptography` does not ship, because encrypted PDFs are refused.

- **Not used:** html2text (GPL-3.0), PyMuPDF and pymupdf4llm (AGPL), Unstructured (sends analytics on import), docling (downloads models, needs torch), `docx2txt` (loses headings), and all community loaders.
- **Upload:** `multipart/form-data`. Limits: 64 MiB for each request, 32 MiB for each PDF or DOCX document and 8 MiB for each HTML document.
- **Order:** parse, then sanitize, then chunk. The `content_hash` of the document is the hash of the raw bytes.
- **Spans and chunk IDs:** `PageSpan` for PDF, `BlockSpan` for DOCX and `LineSpan` (lines of the HTML source) for HTML. Chunk IDs use the heading path, or for a PDF with no outline, the absolute page number. The citation check adds `#page=N` to PDF URIs.
- **Scanned PDFs:** index the pages with at least `min_page_chars` non-space characters (default 20). Report `pages_total`, `pages_indexed` and `skipped_pages` (page reason `no_text`). Fail the document with `no_text_layer` only when no page has text.
- **Refused documents:** encrypted PDFs (`encrypted`), and DOCX documents with macros (`unsupported_media_type`). xat-rag finds macros by the content type and a `vbaProject.bin` part, not by the file name.
- **Hidden text:** the DOCX parser removes hidden text (`w:vanish`). The HTML parser removes hidden elements. Invisible PDF text is not detected in v1.
- **Limits in the process:**
  - One binary parse at a time.
  - A deadline for each document. The parsers check it between pages, blocks, sections and comments.
  - PDF: at most 2000 pages, 200k characters for each page, 20M for each document, and lower decompression limits.
  - DOCX: zip checks before the parse (10k entries, 256 MiB uncompressed, 64 MiB for `word/document.xml`, ratio 100). `lxml` with `resolve_entities=False` and `huge_tree=False`.
  - A passed limit gives `parse_error` with the reason `limit_exceeded`.
  - HTML: at most 8 MiB and 500k `<` characters. Each element starts with `<`, so this is an upper bound for the elements.
- **After a deadline:** a parser that reaches its check gives `parse_error` with the reason `parse_timeout`. A Python thread cannot be stopped. If a parse runs longer than twice the deadline plus 300 seconds, the service enters the degraded state until the app restarts it. `/health/ready` returns 503 with `status: "degraded"`, and new ingestion jobs get 503 `service_unavailable_error`. See [architecture, section 5](../architecture.md#5-ingestion-flow).

## Consequences

- No extra processes, so packaging stays simple.
- A document that makes a parser loop can block ingestion until the service restarts. A document that uses too much memory can stop the whole service, because there is no memory limit. The index stays correct, because writes are atomic.
- pypdf holds the GIL during a long page, so context requests can be slow and their stage time limits can fire late.
- Invisible PDF text can carry prompt injection. Delimiters and citation checks reduce the risk ([ADR 0010](0010-untrusted-content-privacy-and-ingestion.md)).
- `lxml` is a native library. It must be signed and notarized with the app.
- pypdf security fixes must be applied at once, outside the monthly upgrade batch.
- Tables in PDF files become plain text. This can lower retrieval quality for table data.
- Points to check in the implementation: the names of the pypdf decompression limits, the entity settings of python-docx, and `Tag.sourceline` in beautifulsoup4 for HTML line spans.

## Implementation notes (v0.1)

- `pdf-v1`: each parse calls `pypdf.apply_configuration`, so the settings hold only for that parse. The configuration limits the decompression output to 32 MiB, sets `jbig2dec_binary` to `None` and sets `disable_legacy_handling`. The parser version holds the pypdf version.
- `docx-v2`: the parser reads the WordprocessingML elements directly.
  - Tracked insertions are kept, and the result has the note `tracked_insertions_kept`.
  - Deleted text (`w:del` and `w:moveFrom`) and field codes are dropped.
  - Hidden runs are removed. A run is hidden with `w:vanish` or with a character style that hides text.
  - A `w:gridSpan` is never expanded into cells. A value above 64 is a limit error.
  - Comments, headers and footers are skipped, and the result has a note for each.
- `html-v2`: the parser walks the tree once. `pre` keeps its line breaks, and `head` is not removed as a tag. The hidden styles are `display: none`, `visibility: hidden`, `opacity: 0` and `font-size: 0`.
- `diff-v1`: the sanitizer runs after `unidiff` has parsed the text. A text with no file changes is invalid. A binary file is skipped with a note, and so is a file without hunks.
- Text that is not a diff gives `parse_error` with the reason `invalid`.
- A PDF page has a text layer when it has at least `min_page_chars` non-space characters.
- The DOCX parser does not set the `lxml` options `resolve_entities=False` and `huge_tree=False`. The decision above is not met yet. This is an open item.
- The HTML line spans use `Tag.sourceline`.
