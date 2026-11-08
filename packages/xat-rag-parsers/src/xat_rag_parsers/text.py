"""`TextParser` (`text-v1`): one section. The chunker splits it."""

from xat_rag_core.model import LineSpan, ParsedDocument, RawDocument, Section
from xat_rag_core.parsing import Deadline, decode_text, source_document
from xat_rag_core.sanitize import sanitize_text


class TextParser:
    """Plain text: one section with the title as its header."""

    @property
    def kind(self) -> str:
        """The source kind."""
        return "text"

    @property
    def version(self) -> str:
        """The parser version."""
        return "text-v1"

    def parse(self, raw: RawDocument, deadline: Deadline) -> ParsedDocument:
        """Parse the document."""
        deadline.check()
        text = sanitize_text(decode_text(raw.data))
        title = raw.title or raw.doc_id
        section = Section("t", "section", title, text, LineSpan(1, max(1, text.count("\n") + 1)))
        return ParsedDocument(source_document(raw, title), (section,))
