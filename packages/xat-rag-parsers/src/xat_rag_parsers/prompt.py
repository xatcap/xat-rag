"""`PromptParser` (`prompt-v1`): a prompt file as one section that is never split or pruned."""

from xat_rag_core.model import LineSpan, ParsedDocument, RawDocument, Section
from xat_rag_core.parsing import Deadline, decode_text, source_document
from xat_rag_core.sanitize import sanitize_text


class PromptParser:
    """A prompt file. It stays one whole chunk. A prompt that is too large is a `limit_exceeded` error."""

    @property
    def kind(self) -> str:
        """The source kind."""
        return "prompt"

    @property
    def version(self) -> str:
        """The parser version."""
        return "prompt-v1"

    def parse(self, raw: RawDocument, deadline: Deadline) -> ParsedDocument:
        """Parse the document."""
        deadline.check()
        text = sanitize_text(decode_text(raw.data)).strip("\n")
        title = raw.title or raw.doc_id
        section = Section(
            "prompt", "prompt", f"Prompt {title}", text, LineSpan(1, max(1, text.count("\n") + 1)), splittable=False
        )
        return ParsedDocument(source_document(raw, title), (section,))
