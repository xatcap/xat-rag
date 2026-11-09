# xat-rag-documents

Docling parsers of xat-rag for PDF, DOCX, HTML and Excel. See [how it works, step 3](../../docs/how-it-works.md#step-3-parse).

| Parser | Kind | Sections |
|---|---|---|
| `PdfParser` (`pdf-v2`) | `pdf` | One section for each heading that the layout model finds. A section can start in the middle of a page and cross pages. A PDF with no heading is one section. Pages with too little text are skipped and reported. |
| `DocxParser` (`docx-v3`) | `docx` | One section for each heading style. Tables keep their rows. Deleted and hidden text, and headers and footers, are left out. |
| `HtmlParser` (`html-v3`) | `html` | One section for each heading. Navigation and page footers are left out. |
| `XlsxParser` (`xlsx-v1`) | `xlsx` | One section for each sheet. The sheet name is the heading. The rows of the sheet are the span. |

Docling reads all four formats. Each parser version includes the Docling version. The PDF parser uses the layout and table models from the models folder. OCR is off, and nothing is downloaded.

The parsers run in the process that calls xat-rag. `DocumentLimits` holds the limits. The [API reference](../../docs/api.md#limits) lists them.

A passed limit gives `parse_error` with the reason `limit_exceeded`. The parsers never open paths or URLs. Macro-enabled DOCX and Excel files are refused with `unsupported_media_type`.
