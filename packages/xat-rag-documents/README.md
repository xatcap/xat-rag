# xat-rag-documents

PDF, DOCX and HTML parsers of xat-rag. See [ADR 0012](../../docs/adr/0012-document-parsers.md) and the [architecture, section 5](../../docs/architecture.md#pdf-docx-and-html).

| Parser | Library | Sections |
|---|---|---|
| `PdfParser` (`pdf-v1`) | `pypdf` | One section for each outline heading, or one section for each page (`p/<N>`). Pages with no text layer are skipped and reported. |
| `DocxParser` (`docx-v2`) | `python-docx` | One section for each heading style. Tables become pipe rows. Tracked insertions are kept, with a note. Deleted and hidden text is removed. |
| `HtmlParser` (`html-v2`) | `beautifulsoup4` with `html.parser` | One section for each `h1` to `h6`. Scripts, styles and hidden elements are removed. |

The parsers run in the process that calls xat-rag. `DocumentLimits` holds the limits:

- PDF: 2000 pages, 200k characters for each page, 20M characters for each document, and a decompression limit of 32 MiB. Encrypted files are refused.
- DOCX: 10k zip entries, 256 MiB uncompressed, 64 MiB for `word/document.xml`, and a compression ratio of 100. Files with macros are refused.
- HTML: 8 MiB, and 500k `<` characters.

A passed limit gives `parse_error` with the reason `limit_exceeded`. The parsers never open paths or URLs. The DOCX parser does not set the `lxml` options yet. This is an open item.
