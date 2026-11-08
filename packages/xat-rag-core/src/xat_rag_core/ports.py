"""The custom ports of xat-rag. LangChain classes are the ports where LangChain has a matching interface.

See the architecture, section 3.
"""

from typing import Protocol

from xat_rag_core.query import RetrievalQuery


class TokenCounter(Protocol):
    """Counts tokens with the tokenizer of the generator."""

    @property
    def tokenizer_id(self) -> str:
        """The ID of the tokenizer, for example `ministral-3-3b-tokenizer@<sha12>` (from the model lock)."""
        ...

    def count(self, text: str) -> int:
        """Return the number of tokens in `text`."""
        ...


class QueryBuilder[InputT](Protocol):
    """Builds a retrieval query from an input, in a deterministic way."""

    @property
    def name(self) -> str:
        """The name of the builder in recipes, for example `log-v1`."""
        ...

    def build(self, query_input: InputT) -> RetrievalQuery:
        """Return the retrieval query. The same input gives the same query."""
        ...
