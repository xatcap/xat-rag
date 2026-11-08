"""`RagIndex`: one RAG.

Ingestion: parse, chunk, embed, store. Retrieval: lexical, dense and exact search, RRF, rerank, MMR.
"""

import time
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass

import numpy as np
from langchain_core.documents import Document
from langchain_core.runnables import RunnableConfig
from xat_rag_core import lc_codec
from xat_rag_core.errors import InvalidRequestError, ParseError, StageTimeoutError
from xat_rag_core.fusion import reciprocal_rank_fusion
from xat_rag_core.mmr import mmr_order, remove_duplicates
from xat_rag_core.model import Candidate, Chunk, RawDocument, SourceKind, StageScores
from xat_rag_core.parsing import Deadline, SectionParser
from xat_rag_core.query import RetrievalQuery
from xat_rag_core.tracing import no_tracing
from xat_rag_documents import DocumentLimits, DocxParser, HtmlParser, PdfParser
from xat_rag_onnx import OnnxCrossEncoderCompressor, OnnxEmbeddings, OnnxModel
from xat_rag_parsers import (
    ChunkLimits,
    DiffParser,
    JiraParser,
    MarkdownParser,
    PromptParser,
    StructuralChunker,
    TextParser,
)
from xat_rag_parsers.chunker import SPLITTER_VERSION
from xat_rag_sqlite.lexical import LEXICAL_TOKENIZER_ID
from xat_rag_sqlite.manifest import EmbedderInfo, IndexManifest
from xat_rag_sqlite.retrievers import ExactLookupRetriever, Fts5Retriever, VecRetriever
from xat_rag_sqlite.store import SqliteRagStore, UpsertStatus
from xat_rag_tokenizers import HfTokenCounter

from xat_rag.recipes import Limits, RagRecipe
from xat_rag.types import DocumentResult

_NO_CALLBACKS: RunnableConfig = {"callbacks": []}


def make_parser(kind: str, recipe: RagRecipe, lock: AbstractContextManager[object] | None) -> SectionParser:
    """Return the parser of a source kind."""
    source = recipe.source_for(kind)
    limits = DocumentLimits(min_page_chars=source.min_page_chars)
    parsers: Mapping[str, SectionParser] = {
        "markdown": MarkdownParser(),
        "text": TextParser(),
        "diff": DiffParser(),
        "jira": JiraParser(),
        "prompt": PromptParser(),
        "pdf": PdfParser(limits, lock),
        "docx": DocxParser(limits, lock),
        "html": HtmlParser(limits, lock),
    }
    return parsers[kind]


def chunker_version(parser: SectionParser) -> str:
    """Return the chunker version for the manifest: the parser version and the splitter version."""
    return f"{parser.version}+lcts{SPLITTER_VERSION}"


@dataclass(frozen=True, slots=True)
class Retrieved:
    """The candidates of one RAG, best first, and the duplicates that were removed."""

    candidates: tuple[Candidate, ...]
    duplicates: tuple[str, ...]


class _Timer:
    def __init__(self, stage: str, limit_ms: int) -> None:
        self._stage = stage
        self._limit = limit_ms
        self._start = time.monotonic()

    def check(self) -> None:
        if (time.monotonic() - self._start) * 1000 > self._limit:
            raise StageTimeoutError(self._stage, self._limit)


class RagIndex:
    """One RAG: its recipe, its store, its parsers and its models."""

    def __init__(
        self,
        recipe: RagRecipe,
        store: SqliteRagStore,
        embedder: OnnxEmbeddings,
        counter: HfTokenCounter,
        reranker: OnnxModel | None,
        *,
        parse_lock: AbstractContextManager[object] | None = None,
        parse_deadline_seconds: float = 60.0,
    ) -> None:
        """Make the index. Use `XatRag.open` in place of this constructor."""
        self.recipe = recipe
        self.store = store
        self._embedder = embedder
        self._counter = counter
        self._reranker = reranker
        self._deadline_seconds = parse_deadline_seconds
        kinds = {source.kind for source in recipe.sources.values()}
        self._parsers = {kind: make_parser(kind, recipe, parse_lock) for kind in kinds}
        self._chunkers = {
            kind: StructuralChunker(
                counter.count,
                embedder.count_document_tokens,
                ChunkLimits(recipe.source_for(kind).max_chunk_tokens, max_embed_tokens=510),
            )
            for kind in kinds
        }

    @property
    def name(self) -> str:
        """The name of the RAG."""
        return self.recipe.rag.name

    def check_kinds(self, kinds: set[str]) -> None:
        """Raise `InvalidRequestError` if the RAG has no source for one of the kinds."""
        if kinds - set(self._parsers):
            raise InvalidRequestError("The RAG has no source for a kind in the upload.")

    @staticmethod
    def manifest(recipe: RagRecipe, embedder: OnnxEmbeddings, embedder_sha256: str, tokenizer_id: str) -> IndexManifest:
        """Return the manifest that the index must have for this recipe and these models."""
        kinds = sorted({source.kind for source in recipe.sources.values()})
        return IndexManifest(
            rag=recipe.rag.name,
            embedder=EmbedderInfo(
                model_id=embedder.model_id,
                sha256=embedder_sha256,
                dims=embedder.dims,
                normalize=True,
                query_prefix=embedder.query_prefix,
                document_prefix=embedder.document_prefix,
            ),
            lexical_tokenizer=LEXICAL_TOKENIZER_ID,
            generator_tokenizer_id=tokenizer_id,
            index_hash=recipe.index_hash(),
            chunkers={kind: chunker_version(make_parser(kind, recipe, None)) for kind in kinds},
        )

    # Ingestion.

    def ingest(self, raw: RawDocument) -> DocumentResult:
        """Parse, chunk, embed and store one document. A parse problem goes into the result, not an exception."""
        if raw.rag != self.name:
            raise InvalidRequestError("A document was sent to another RAG than its own.")
        parser = self._parsers.get(raw.source_kind.value)
        if parser is None:
            raise InvalidRequestError("The RAG has no source of this kind.")
        try:
            with no_tracing():
                parsed = parser.parse(raw, Deadline(self._deadline_seconds))
                chunks = self._chunkers[raw.source_kind.value].chunk(parsed, chunker_version(parser))
        except ParseError as error:
            return DocumentResult(
                raw.doc_id,
                "failed",
                error_type=error.error_type.value,
                reason=error.reason.value,
                message=error.message,
            )
        if not chunks:
            return DocumentResult(
                raw.doc_id,
                "failed",
                error_type="parse_error",
                reason="invalid",
                message="The document has no text to index.",
            )
        plan = self.store.upsert(parsed.source, chunks, self._embed_chunks)
        report = parsed.report
        return DocumentResult(
            doc_id=raw.doc_id,
            status=plan.status.value,
            chunks=len(chunks),
            embedded=0 if plan.status is UpsertStatus.UNCHANGED else len(plan.to_embed),
            pages_total=report.pages_total,
            pages_indexed=report.pages_indexed,
            skipped_pages=report.skipped_pages,
            notes=report.notes,
        )

    def _embed_chunks(self, chunks: tuple[Chunk, ...]):
        return self._embedder.embed_documents_np([f"{chunk.header}\n{chunk.text}" for chunk in chunks])

    def delete(self, doc_id: str) -> bool:
        """Delete a document. Call `checkpoint` after a batch of deletes."""
        return self.store.delete_document(doc_id)

    # Retrieval.

    def retrieve(self, query: RetrievalQuery, limits: Limits) -> Retrieved:
        """Run the lexical, dense and exact searches, fuse them with RRF, rerank, and choose with MMR."""
        settings = self.recipe.retrieval
        with no_tracing():
            timer = _Timer("retrieve", limits.retrieve)
            lexical = Fts5Retriever(store=self.store, k=settings.lexical_k, filters=query.filters).invoke(
                " ".join(query.lexical_terms), config=_NO_CALLBACKS
            )
            exact = ExactLookupRetriever(
                store=self.store, lookups=query.exact_lookups, k=settings.exact_k, filters=query.filters
            ).invoke("", config=_NO_CALLBACKS)
            timer.check()
            timer = _Timer("embed", limits.embed)
            dense = VecRetriever(
                store=self.store, embeddings=self._embedder, k=settings.dense_k, filters=query.filters
            ).invoke(query.dense_text, config=_NO_CALLBACKS)
            timer.check()
            candidates = self._fuse(lexical, dense, exact)[: settings.rerank_candidates]
            if self._reranker is not None and candidates:
                timer = _Timer("rerank", limits.rerank)
                candidates = self._rerank(candidates, query.rerank_text or query.dense_text)
                timer.check()
        return self._choose(candidates)

    def _fuse(
        self, lexical: Sequence[Document], dense: Sequence[Document], exact: Sequence[Document]
    ) -> list[Candidate]:
        lists = {"exact": exact, "lexical": lexical, "dense": dense}
        by_id: dict[str, Chunk] = {}
        for documents in lists.values():
            for document in documents:
                by_id.setdefault(lc_codec.chunk_id_of(document), lc_codec.document_to_chunk(document))
        fused = reciprocal_rank_fusion(
            {name: [lc_codec.chunk_id_of(d) for d in docs] for name, docs in lists.items()},
            k=self.recipe.retrieval.rrf_k,
        )
        return [
            Candidate(
                by_id[item.chunk_id],
                StageScores(
                    lexical_rank=item.ranks.get("lexical"),
                    dense_rank=item.ranks.get("dense"),
                    exact_rank=item.ranks.get("exact"),
                    rrf_score=item.score,
                ),
            )
            for item in fused
        ]

    def _rerank(self, candidates: list[Candidate], text: str) -> list[Candidate]:
        assert self._reranker is not None
        compressor = OnnxCrossEncoderCompressor(
            model=self._reranker, model_id=self.recipe.rag.reranker, top_n=len(candidates)
        )
        documents = [lc_codec.candidate_to_document(candidate) for candidate in candidates]
        ranked = compressor.compress_documents(documents, text, callbacks=[])
        return [lc_codec.document_to_candidate(document) for document in ranked]

    def _choose(self, candidates: list[Candidate]) -> Retrieved:
        if not candidates:
            return Retrieved((), ())
        # A concurrent delete can remove a candidate after the search. Such candidates are left out.
        found = self.store.vectors_found([candidate.chunk_id for candidate in candidates])
        candidates = [candidate for candidate in candidates if candidate.chunk_id in found]
        if not candidates:
            return Retrieved((), ())
        ids = [candidate.chunk_id for candidate in candidates]
        vectors = np.stack([found[chunk_id] for chunk_id in ids])
        kept = remove_duplicates([c.chunk.content_hash for c in candidates], vectors)
        kept_set = set(kept)
        duplicates = tuple(ids[i] for i in range(len(ids)) if i not in kept_set)
        relevance = [_relevance(candidates[i]) for i in kept]
        order = mmr_order(relevance, vectors[kept], self.recipe.retrieval.keep, self.recipe.retrieval.mmr_lambda)
        return Retrieved(tuple(candidates[kept[i]] for i in order), duplicates)


def _relevance(candidate: Candidate) -> float:
    scores = candidate.scores
    if scores.rerank_score is not None:
        return scores.rerank_score
    return scores.rrf_score or 0.0


def source_kind(value: str) -> SourceKind:
    """Return the source kind for a name. Raise `InvalidRequestError` for an unknown kind."""
    try:
        return SourceKind(value)
    except ValueError as error:
        raise InvalidRequestError("The source kind is unknown.") from error
