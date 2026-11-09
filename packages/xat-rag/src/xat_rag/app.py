"""`XatRag`: the public library facade and the composition root.

The facade takes and returns xat-rag types only.
"""

import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType

from xat_rag_core.errors import (
    ConfigError,
    IndexMismatchError,
    InternalError,
    NotFoundError,
    XatRagError,
)
from xat_rag_core.model import RawDocument, SourceKind
from xat_rag_core.query import RetrievalQuery
from xat_rag_sqlite.store import SqliteRagStore

from xat_rag.index import RagIndex, Retrieved, split_rules_id
from xat_rag.models import LoadedModels, load_models
from xat_rag.recipes import ContextRecipe, Limits, RagRecipe, load_recipes
from xat_rag.retrieve import ContextRequest, retrieve, retrieved_chunks
from xat_rag.settings import Settings
from xat_rag.types import DocumentResult, RetrievalResult, SearchHit

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
    ) -> None:
        """Use `XatRag.open` in place of this constructor."""
        self.settings = settings
        self._contexts = MappingProxyType(dict(contexts))
        self._models = models
        self._rags = MappingProxyType(dict(rags))
        self._parse_lock = threading.Lock()  # One binary parse at a time.
        self._boundary_lock = threading.BoundedSemaphore(1)  # One split-point batch at a time, so queries can run.
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
        """Check the parts of the recipes that the recipe reader cannot check. Raise `ConfigError`."""
        for recipe in self._contexts.values():
            section = recipe.context
            names = [source.rag for source in section.sources]
            if len(set(names)) != len(names):
                raise ConfigError(f"The context '{section.name}' names a RAG twice.")
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
        counter = models.size_counter
        sha = models.embedder_sha256[recipe.rag.embedder]
        identity = f"{embedder.model_id}-{sha[:12]}" if self.settings.semantic_split else None
        rules = split_rules_id(identity, self.settings.overlap_share)
        store = SqliteRagStore(self._path(name), RagIndex.manifest(recipe, embedder, sha, counter.tokenizer_id, rules))
        return RagIndex(
            recipe,
            store,
            embedder,
            counter,
            models.rerankers.get(recipe.rag.reranker),
            parse_lock=self._parse_lock,
            parse_deadline_seconds=self.settings.parse_deadline_seconds,
            models_folder=self.settings.models_folder,
            boundary_lock=self._boundary_lock,
            split_rules=rules,
            semantic_split=self.settings.semantic_split,
            overlap_share=self.settings.overlap_share,
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
        """Read the recipes, check and load the models, and open the indexes.

        Raise `ConfigError` for a bad recipe or model, and `IndexMismatchError` for an index that
        needs a rebuild.
        """
        rags, contexts = load_recipes(settings.recipes_folder)
        models = load_models(
            settings.models_folder,
            settings.lock_file,
            settings.data_folder / "model-check-cache.json",
            embedders={recipe.rag.embedder for recipe in rags.values()},
            rerankers={recipe.rag.reranker for recipe in rags.values()},
            threads=settings.threads,
            layout_models=any(source.kind == "pdf" for recipe in rags.values() for source in recipe.sources.values()),
        )
        return XatRag(settings, rags, contexts, models)

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

    def search_hits(self, rag: str, query: RetrievalQuery) -> tuple[SearchHit, ...]:
        """Return the public results of a search in one RAG, with the scores of each stage."""
        index = self.index(rag)
        candidates = self.search(rag, query).candidates
        scores = {candidate.chunk_id: candidate.scores for candidate in candidates}
        return tuple(SearchHit(chunk, scores[chunk.chunk_id]) for chunk in retrieved_chunks(candidates, {rag: index}))

    # Contexts.

    def retrieve(self, request: ContextRequest) -> RetrievalResult:
        """Return the ranked chunks of a context request, each with its source and metadata."""
        recipe = self.context_recipe(request.context)
        indexes = {source.rag: self.index(source.rag) for source in recipe.context.sources}
        return retrieve(request, recipe, indexes=indexes)

    def context_recipe(self, context: str) -> ContextRecipe:
        """Return the recipe of a context. Raise `NotFoundError` if it does not exist."""
        recipe = self._contexts.get(context)
        if recipe is None:
            raise NotFoundError("The context does not exist.")
        return recipe

    @property
    def data_folder(self) -> Path:
        """The data folder."""
        return self.settings.data_folder
