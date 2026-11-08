"""`XatRag`: the public library facade and the composition root. See ADR 0003.

The facade takes and returns xat-rag types only. `xat_rag.langchain` gives the LangChain objects.
"""

import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType

from xat_rag_core.budget import GeneratorProfile
from xat_rag_core.citations import CitationCheck, check_answer
from xat_rag_core.errors import (
    ConfigError,
    IndexMismatchError,
    InternalError,
    InvalidRequestError,
    NotFoundError,
    XatRagError,
)
from xat_rag_core.model import RawDocument, SourceKind
from xat_rag_core.query import RetrievalQuery
from xat_rag_sqlite.store import SqliteRagStore

from xat_rag.compose import ContextRequest, chunk_uri, compose
from xat_rag.index import RagIndex, Retrieved
from xat_rag.models import LoadedModels, load_models
from xat_rag.prompts import Prompt, PromptStore
from xat_rag.recipes import ContextRecipe, Limits, RagRecipe, load_recipes
from xat_rag.settings import Settings
from xat_rag.types import ContextPack, DocumentResult

TEXT_KINDS = frozenset({SourceKind.MARKDOWN, SourceKind.TEXT, SourceKind.DIFF, SourceKind.JIRA, SourceKind.PROMPT})
MAX_TEXT_DOCUMENT_BYTES = 4 * 1024 * 1024


class XatRag:
    """The xat-rag library. Open it once, and share it between threads."""

    def __init__(
        self,
        settings: Settings,
        rags: Mapping[str, RagRecipe],
        contexts: Mapping[str, ContextRecipe],
        models: LoadedModels,
        prompts: PromptStore,
    ) -> None:
        """Use `XatRag.open` in place of this constructor."""
        self.settings = settings
        self._contexts = MappingProxyType(dict(contexts))
        self._models = models
        self._prompts = prompts
        self._rags = MappingProxyType(dict(rags))
        self._parse_lock = threading.Lock()  # One binary parse at a time (ADR 0012).
        self._lock = threading.Lock()
        self._indexes: dict[str, RagIndex] = {}
        self._mismatched: dict[str, str] = {}
        settings.data_folder.mkdir(parents=True, exist_ok=True)
        self._check_recipes()
        for name in rags:
            try:
                self._indexes[name] = self._open_index(name)
            except (IndexMismatchError, InternalError) as error:
                # A changed or damaged index needs a rebuild. Its routes give index_mismatch_error until then.
                self._mismatched[name] = error.message

    def _check_recipes(self) -> None:
        """Check the parts of the recipes that need the models or the prompts. Raise `ConfigError`."""
        for recipe in self._contexts.values():
            section = recipe.context
            names = [source.rag for source in section.sources]
            if len(set(names)) != len(names):
                raise ConfigError(f"The context '{section.name}' names a RAG twice.")
            profile = self._models.profiles[section.generator]
            if section.window > profile.context_window:
                raise ConfigError(f"The window of the context '{section.name}' is larger than its generator allows.")
            for prompt_id in section.pinned:
                try:
                    self._prompts.get(prompt_id)
                except NotFoundError as error:
                    raise ConfigError(f"The context '{section.name}' pins a prompt that is not installed.") from error
        for recipe in self._rags.values():
            kinds = [source.kind for source in recipe.sources.values()]
            if len(set(kinds)) != len(kinds):
                raise ConfigError(f"The RAG '{recipe.rag.name}' has two sources of the same kind.")

    def _path(self, name: str) -> Path:
        return self.settings.data_folder / f"{name}.sqlite"

    def _open_index(self, name: str) -> RagIndex:
        recipe = self._rags[name]
        models = self._models
        embedder = models.embedders[recipe.rag.embedder]
        counter = models.counters[recipe.rag.generator]
        sha = models.embedder_sha256[recipe.rag.embedder]
        store = SqliteRagStore(self._path(name), RagIndex.manifest(recipe, embedder, sha, counter.tokenizer_id))
        return RagIndex(
            recipe,
            store,
            embedder,
            counter,
            models.rerankers.get(recipe.rag.reranker),
            parse_lock=self._parse_lock,
            parse_deadline_seconds=self.settings.parse_deadline_seconds,
        )

    def rebuild(self, rag: str) -> None:
        """Delete the index of a RAG and make an empty one. The app must send the documents again.

        xat-rag keeps no raw documents, so it cannot embed them again itself.
        """
        if rag not in self._rags:
            raise NotFoundError("The RAG does not exist.")
        with self._lock:
            old = self._indexes.pop(rag, None)
            self._mismatched[rag] = "The RAG is being rebuilt."
        try:
            if old is not None:
                old.store.close()
            for suffix in ("", "-wal", "-shm"):
                path = Path(f"{self._path(rag)}{suffix}")
                if path.exists():
                    path.unlink()
            index = self._open_index(rag)
        except XatRagError as error:
            with self._lock:
                self._mismatched[rag] = error.message
            raise
        with self._lock:
            self._indexes[rag] = index
            self._mismatched.pop(rag, None)

    def needs_rebuild(self) -> dict[str, str]:
        """Return the RAGs whose index does not match, with the reason."""
        with self._lock:
            return dict(self._mismatched)

    @staticmethod
    def open(settings: Settings) -> "XatRag":
        """Read the recipes and prompts, check and load the models, and open the indexes.

        Raise `ConfigError` for a bad recipe or model, and `IndexMismatchError` for an index that
        needs a rebuild.
        """
        rags, contexts = load_recipes(settings.recipes_folder)
        generators = {recipe.rag.generator for recipe in rags.values()} | {
            recipe.context.generator for recipe in contexts.values()
        }
        models = load_models(
            settings.models_folder,
            settings.lock_file,
            settings.data_folder / "model-check-cache.json",
            embedders={recipe.rag.embedder for recipe in rags.values()},
            rerankers={recipe.rag.reranker for recipe in rags.values()},
            generators=generators,
            threads=settings.threads,
        )
        return XatRag(settings, rags, contexts, models, PromptStore(settings.prompts_folder))

    def close(self) -> None:
        """Close all indexes."""
        with self._lock:
            indexes = list(self._indexes.values())
        for index in indexes:
            index.store.close()

    # RAGs and documents.

    def rag_names(self) -> list[str]:
        """Return the names of the RAGs."""
        return sorted(self._rags)

    def context_names(self) -> list[str]:
        """Return the names of the contexts."""
        return sorted(self._contexts)

    def index(self, rag: str) -> RagIndex:
        """Return a RAG. Raise `NotFoundError` if it does not exist, and `IndexMismatchError` if it needs a rebuild."""
        with self._lock:
            mismatch = self._mismatched.get(rag)
            found = self._indexes.get(rag)
        if mismatch is not None:
            raise IndexMismatchError(mismatch)
        if found is None:
            raise NotFoundError("The RAG does not exist.")
        return found

    def ingest(self, documents: Sequence[RawDocument]) -> list[DocumentResult]:
        """Ingest documents, one transaction for each document. One bad document does not stop the others."""
        results: list[DocumentResult] = []
        for document in documents:
            index = self.index(document.rag)
            if document.source_kind in TEXT_KINDS and len(document.data) > MAX_TEXT_DOCUMENT_BYTES:
                results.append(
                    DocumentResult(
                        document.doc_id,
                        "failed",
                        error_type="parse_error",
                        reason="too_large",
                        message="The document is larger than 4 MiB.",
                    )
                )
                continue
            results.append(index.ingest(document))
        return results

    def ingest_one(self, document: RawDocument) -> DocumentResult:
        """Ingest one document."""
        return self.ingest([document])[0]

    def chunk_uris(self, chunk_ids: Sequence[str]) -> dict[str, str]:
        """Return the URI of each known chunk. A PDF chunk URI ends with `#page=N`."""
        result: dict[str, str] = {}
        with self._lock:
            indexes = list(self._indexes.values())
        for index in indexes:
            for chunk_id, chunk in index.store.get_chunks(chunk_ids).items():
                result.setdefault(chunk_id, chunk_uri(chunk))
        return result

    def delete(self, rag: str, doc_id: str) -> bool:
        """Delete a document, then write the WAL into the file so that no deleted text stays in it."""
        index = self.index(rag)
        deleted = index.delete(doc_id)
        if deleted:
            index.store.checkpoint()
        return deleted

    def checkpoint(self, rag: str) -> None:
        """Write the WAL of a RAG into its file. Call this after a batch of updates."""
        self.index(rag).store.checkpoint()

    def search(self, rag: str, query: RetrievalQuery, context: str | None = None) -> Retrieved:
        """Return the ranked candidates of one RAG, with the scores of each stage. For debug and evaluation.

        The stage time limits come from `context`, or the default limits when no context is given.
        """
        index = self.index(rag)
        limits = self.context_recipe(context).limits if context is not None else Limits()
        return index.retrieve(query, limits)

    # Contexts.

    def compose(self, request: ContextRequest) -> ContextPack:
        """Make a context pack. The app sends its messages to the generator itself (ADR 0008)."""
        recipe = self._contexts.get(request.context)
        if recipe is None:
            raise NotFoundError("The context does not exist.")
        generator = self._models.profiles[recipe.context.generator]
        counter = self._models.counters[recipe.context.generator]
        indexes = {source.rag: self.index(source.rag) for source in recipe.context.sources}
        return compose(request, recipe, indexes=indexes, prompts=self._prompts, counter=counter, generator=generator)

    def context_recipe(self, context: str) -> ContextRecipe:
        """Return the recipe of a context. Raise `NotFoundError` if it does not exist."""
        recipe = self._contexts.get(context)
        if recipe is None:
            raise NotFoundError("The context does not exist.")
        return recipe

    def generator_profile(self, generator: str) -> GeneratorProfile:
        """Return the profile of a loaded generator."""
        found = self._models.profiles.get(generator)
        if found is None:
            raise InvalidRequestError("The generator has no profile.")
        return found

    def count_tokens(self, text: str, generator: str) -> int:
        """Count tokens with the tokenizer of a generator."""
        counter = self._models.counters.get(generator)
        if counter is None:
            raise InvalidRequestError("The generator has no profile.")
        return counter.count(text)

    def check_citations(self, answer: str, chunks: Mapping[str, str]) -> CitationCheck:
        """Check an answer against the chunk list of its pack (label to chunk ID). Each cited chunk must still exist."""
        with self._lock:
            stores = [index.store for index in self._indexes.values()]
        return check_answer(answer, chunks, lambda chunk_id: any(store.chunk_exists(chunk_id) for store in stores))

    def prompt(self, prompt_id: str) -> Prompt:
        """Return a pinned prompt. Raise `NotFoundError` if it does not exist."""
        return self._prompts.get(prompt_id)

    @property
    def data_folder(self) -> Path:
        """The data folder."""
        return self.settings.data_folder
