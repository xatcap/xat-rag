"""Pinned prompts: files that the app installs in the prompts folder. The API can only read them. See ADR 0010.

A file `log-triage-system@3.md` (or `.txt`) is the prompt `prompt:log-triage-system@3`.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from xat_rag_core.errors import ConfigError, NotFoundError
from xat_rag_core.sanitize import sanitize_text

_FILE = re.compile(r"([a-z0-9][a-z0-9-]{0,63})@([0-9]{1,6})\.(?:md|txt)")
MAX_PROMPT_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class Prompt:
    """One pinned prompt."""

    name: str
    version: int
    text: str

    @property
    def prompt_id(self) -> str:
        """The ID, for example `prompt:log-triage-system@3`."""
        return f"prompt:{self.name}@{self.version}"


class PromptStore:
    """The pinned prompts, read once at startup."""

    def __init__(self, folder: Path | None) -> None:
        """Read the prompts folder. A folder of `None` means no pinned prompts."""
        self._prompts: dict[str, Prompt] = {}
        if folder is None:
            return
        if not folder.is_dir():
            raise ConfigError(f"The prompts folder '{folder}' does not exist.")
        for path in sorted(folder.iterdir()):
            match = _FILE.fullmatch(path.name)
            if match is None:
                continue
            if path.stat().st_size > MAX_PROMPT_BYTES:
                raise ConfigError(f"The prompt file '{path.name}' is larger than 256 KiB.")
            try:
                text = sanitize_text(path.read_text("utf-8")).strip()
            except (OSError, UnicodeDecodeError) as error:
                raise ConfigError(f"The prompt file '{path.name}' is not valid UTF-8 text.") from error
            prompt = Prompt(match.group(1), int(match.group(2)), text)
            if prompt.prompt_id in self._prompts:
                raise ConfigError(f"Two prompt files give the prompt '{prompt.prompt_id}'. Keep one of them.")
            self._prompts[prompt.prompt_id] = prompt

    def get(self, prompt_id: str) -> Prompt:
        """Return a prompt by ID. Raise `NotFoundError` if it does not exist."""
        prompt = self._prompts.get(prompt_id)
        if prompt is None:
            raise NotFoundError(f"The pinned prompt '{prompt_id}' does not exist in the prompts folder.")
        return prompt

    def ids(self) -> list[str]:
        """Return the IDs of all prompts."""
        return sorted(self._prompts)
