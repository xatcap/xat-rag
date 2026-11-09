"""`TextParser` (`text-v2`): one section. Its paragraphs are the blocks that the chunker splits at."""

from xat_rag_core.model import BlockKind, LineSpan, ParsedDocument, RawDocument, Section, TextBlock
from xat_rag_core.parsing import Deadline, decode_text, source_document
from xat_rag_core.sanitize import sanitize_text


def _paragraphs(text: str) -> tuple[TextBlock, ...]:
    blocks: list[TextBlock] = []
    offset = 0
    start: int | None = None
    end = 0
    for line in text.splitlines(keepends=True):
        if line.strip():
            start = offset if start is None else start
            end = offset + len(line.rstrip())
        elif start is not None:
            blocks.append(TextBlock(BlockKind.PARAGRAPH, start, end))
            start = None
        offset += len(line)
    if start is not None:
        blocks.append(TextBlock(BlockKind.PARAGRAPH, start, end))
    return tuple(blocks)


class TextParser:
    """Plain text: one section with the title as its header."""

    @property
    def kind(self) -> str:
        """The source kind."""
        return "text"

    @property
    def version(self) -> str:
        """The parser version."""
        return "text-v2"

    def parse(self, raw: RawDocument, deadline: Deadline) -> ParsedDocument:
        """Parse the document."""
        deadline.check()
        text = sanitize_text(decode_text(raw.data))
        title = raw.title or raw.doc_id
        section = Section(
            "t", "section", title, text, LineSpan(1, max(1, text.count("\n") + 1)), blocks=_paragraphs(text)
        )
        return ParsedDocument(source_document(raw, title), (section,))
