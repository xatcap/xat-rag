# xat-rag-documents

PDF, DOCX and HTML parsers of xat-rag. See [how it works, step 3](../../docs/how-it-works.md#step-3-parse).

| Parser | Library | Sections |
|---|---|---|
| `PdfParser` (`pdf-v1`) | `pypdf` | One section for each outline heading, or one section for each page (`p/<N>`). Pages with no text layer are skipped and reported. |
| `DocxParser` (`docx-v2`) | `python-docx` | One section for each heading style. Tables become pipe rows. Tracked insertions are kept, with a note. Deleted and hidden text is removed. |
| `HtmlParser` (`html-v2`) | `beautifulsoup4` with `html.parser` | One section for each `h1` to `h6`. Scripts, styles and hidden elements are removed. |

The parsers run in the process that calls xat-rag. `DocumentLimits` holds the limits. The [API reference](../../docs/api.md#limits) lists them.

A passed limit gives `parse_error` with the reason `limit_exceeded`. The parsers never open paths or URLs. The DOCX parser does not set the `lxml` options yet. This is an open item.
