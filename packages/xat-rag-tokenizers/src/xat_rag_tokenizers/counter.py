"""`HfTokenCounter`: counts generator tokens with a locked `tokenizer.json`."""

from pathlib import Path

from tokenizers import Tokenizer
from xat_rag_core.errors import ConfigError


class HfTokenCounter:
    """Counts tokens without special tokens. The chat template overhead is in the generator profile."""

    def __init__(self, tokenizer_file: Path, tokenizer_id: str) -> None:
        """Load the tokenizer from a local file. Check the file against the lock first."""
        try:
            self._tokenizer = Tokenizer.from_file(str(tokenizer_file))
        except Exception as error:
            raise ConfigError(f"The tokenizer file '{tokenizer_file}' could not be loaded.") from error
        self._tokenizer.no_truncation()
        self._tokenizer.no_padding()
        # Text such as "[INST]" in a document is text, not a control token. It is counted as text.
        self._tokenizer.encode_special_tokens = True
        self._id = tokenizer_id

    @property
    def tokenizer_id(self) -> str:
        """The ID of the tokenizer, for the manifest."""
        return self._id

    def count(self, text: str) -> int:
        """Return the number of tokens in `text`."""
        if not text:
            return 0
        return len(self._tokenizer.encode(text, add_special_tokens=False).ids)
