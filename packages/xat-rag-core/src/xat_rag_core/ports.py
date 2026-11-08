"""The custom ports of xat-rag. LangChain classes are the ports where LangChain has a matching interface.

See the architecture, section 3.
"""

from typing import Protocol


class TokenCounter(Protocol):
    """Counts the size of a text with the fixed size ruler of xat-rag."""

    @property
    def tokenizer_id(self) -> str:
        """The ID of the tokenizer, for example `ministral-3-3b-tokenizer@<sha12>` (from the model lock)."""
        ...

    def count(self, text: str) -> int:
        """Return the number of tokens in `text`."""
        ...
