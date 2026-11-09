"""The settings of the xat-rag library."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    """Where xat-rag finds its files, and how it runs.

    `data_folder` holds one SQLite file for each RAG and the model check cache. The app owns it,
    and must protect it, because the indexes hold copies of sensitive text.
    `semantic_split` and `overlap_share` exist for the evaluation of the split rules.
    """

    data_folder: Path
    recipes_folder: Path
    models_folder: Path
    lock_file: Path
    threads: int = 0
    parse_deadline_seconds: float = 60.0
    semantic_split: bool = True
    overlap_share: float = 0.0
