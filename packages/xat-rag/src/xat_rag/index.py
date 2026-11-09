"""`RagIndex`: one RAG.

Ingestion: parse, chunk, embed, store. Retrieval: lexical and dense search, RRF, rerank, MMR.
"""

import itertools
import json
import time
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from xat_rag_core.errors import InvalidRequestError, ParseError, StageTimeoutError
from xat_rag_core.fusion import reciprocal_rank_fusion
from xat_rag_core.ids import sha256_hex
from xat_rag_core.mmr import mmr_order, remove_duplicates
from xat_rag_core.model import Candidate, Chunk, ParseReport, RawDocument, SkippedPage, SourceKind, StageScores
from xat_rag_core.parsing import Deadline, SectionParser
from xat_rag_core.query import RetrievalQuery
from xat_rag_core.ranking import final_score
from xat_rag_documents import DocumentLimits, DocxParser, HtmlParser, PdfParser, XlsxParser, longest_parse_seconds
from xat_rag_onnx import OnnxEmbeddings, OnnxModel, OnnxReranker
from xat_rag_parsers import (
    ChunkLimits,
    DiffParser,
    JiraParser,
    MarkdownParser,
    PromptParser,
    StructuralChunker,
    TextParser,
)
from xat_rag_parsers.chunker import BLOCK_CHUNKER_ID, BLOCK_KINDS, SPLITTER_VERSION
from xat_rag_sqlite.lexical import LEXICAL_TOKENIZER_ID
from xat_rag_sqlite.manifest import EmbedderInfo, IndexManifest
from xat_rag_sqlite.retrievers import Fts5Retriever, VecRetriever
from xat_rag_sqlite.store import SqliteRagStore, UpsertStatus
from xat_rag_tokenizers import HfTokenCounter

from xat_rag.recipes import Limits, RagRecipe
from xat_rag.types import DocumentResult


def make_parser(
    kind: str, recipe: RagRecipe, lock: AbstractContextManager[object] | None, models_folder: Path | None = None
) -> SectionParser:
    """Return the parser of a source kind."""
    source = recipe.source_for(kind)
    limits = DocumentLimits(min_page_chars=source.min_page_chars)
    parsers: Mapping[str, SectionParser] = {
        "markdown": MarkdownParser(),
        "text": TextParser(),
        "diff": DiffParser(),
        "jira": JiraParser(),
        "prompt": PromptParser(),
        "pdf": PdfParser(limits, lock, models_folder),
        "docx": DocxParser(limits, lock),
        "html": HtmlParser(limits, lock),
        "xlsx": XlsxParser(limits, lock),
    }
    return parsers[kind]


def stuck_after_seconds(parse_deadline_seconds: float) -> float:
    """Return the time after which one document counts as stuck."""
    return max(parse_deadline_seconds, longest_parse_seconds()) * 2 + 300


def split_rules_id(embedder_id: str | None, overlap_share: float = 0.0) -> str:
    """Return the part of the chunker version that names the split options of block kinds."""
    semantic = f"+sem1-{embedder_id}" if embedder_id else ""
    overlap = f"+ov{round(overlap_share * 100)}" if overlap_share else ""
    return semantic + overlap


def chunker_version(parser: SectionParser, split_rules: str = "") -> str:
    """Return the chunker version for the manifest: the parser version and the version of the split rules."""
    if parser.kind in BLOCK_KINDS:
        return f"{parser.version}+{BLOCK_CHUNKER_ID}{split_rules}"
    return f"{parser.version}+lcts{SPLITTER_VERSION}"


def _fingerprint(raw: RawDocument, version: str) -> str:
    """Return the identity of an upload: everything that can change the stored document."""
    payload = json.dumps(
        [raw.source_kind.value, raw.uri, raw.title, raw.version, sorted(raw.metadata.items()), version], default=str
    )
    return sha256_hex(payload.encode("utf-8") + b"\0" + raw.data)


def _state_json(
    chunks: int, pages_total: int | None, pages_indexed: int | None, report: ParseReport, notes: Sequence[str]
) -> str:
    return json.dumps(
        {
            "chunks": chunks,
            "pages_total": pages_total,
            "pages_indexed": pages_indexed,
            "skipped_pages": [[p.page, p.label, p.reason] for p in report.skipped_pages],
            "notes": [*report.notes, *notes],
        }
    )


def _unchanged_result(doc_id: str, state: str) -> DocumentResult | None:
    """Return the saved result, or `None` if the row cannot be read, so that the document is parsed again."""
    try:
        data = json.loads(state)
        return DocumentResult(
            doc_id=doc_id,
            status=UpsertStatus.UNCHANGED.value,
            chunks=data["chunks"],
            pages_total=data["pages_total"],
            pages_indexed=data["pages_indexed"],
            skipped_pages=tuple(SkippedPage(*item) for item in data["skipped_pages"]),
            notes=tuple(data["notes"]),
        )
    except (ValueError, KeyError, TypeError):
        return None


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
        models_folder: Path | None = None,
        boundary_lock: AbstractContextManager[object] | None = None,
        split_rules: str = "",
        semantic_split: bool = True,
        overlap_share: float = 0.0,
    ) -> None:
        """Make the index. Use `XatRag.open` in place of this constructor."""
        self.recipe = recipe
        self.store = store
        self._embedder = embedder
        self._counter = counter
        self._reranker = reranker
        self._deadline_seconds = parse_deadline_seconds
        self._boundary_lock = boundary_lock or nullcontext()
        self._split_rules = split_rules
        kinds = {source.kind for source in recipe.sources.values()}
        self._parsers = {kind: make_parser(kind, recipe, parse_lock, models_folder) for kind in kinds}
        self._chunkers = {
            kind: StructuralChunker(
                counter.count,
                embedder.count_document_tokens,
                ChunkLimits(recipe.source_for(kind).max_chunk_tokens, max_embed_tokens=510),
                embed_batch=self._embed_for_split if semantic_split and kind in BLOCK_KINDS else None,
                overlap_share=overlap_share if kind in BLOCK_KINDS else 0.0,
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
    def manifest(
        recipe: RagRecipe, embedder: OnnxEmbeddings, embedder_sha256: str, tokenizer_id: str, split_rules: str = ""
    ) -> IndexManifest:
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
            size_tokenizer_id=tokenizer_id,
            index_hash=recipe.index_hash(),
            chunkers={kind: chunker_version(make_parser(kind, recipe, None), split_rules) for kind in kinds},
        )

    # Ingestion.

    def ingest(self, raw: RawDocument) -> DocumentResult:
        """Parse, chunk, embed and store one document. A parse problem goes into the result, not an exception."""
        if raw.rag != self.name:
            raise InvalidRequestError("A document was sent to another RAG than its own.")
        parser = self._parsers.get(raw.source_kind.value)
        if parser is None:
            raise InvalidRequestError("The RAG has no source of this kind.")
        version = chunker_version(parser, self._split_rules)
        fingerprint = _fingerprint(raw, version) if raw.source_kind.value in BLOCK_KINDS else None
        saved = self.store.ingest_state(raw.doc_id, fingerprint) if fingerprint is not None else None
        unchanged = _unchanged_result(raw.doc_id, saved) if saved is not None else None
        if unchanged is not None:
            return unchanged
        notes: list[str] = []
        try:
            deadline = Deadline(self._deadline_seconds)
            parsed = parser.parse(raw, deadline)
            chunks = self._chunkers[raw.source_kind.value].chunk(
                parsed, version, Deadline(self._deadline_seconds), notes
            )
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
        report = parsed.report
        state = None
        if fingerprint is not None:
            state = (fingerprint, _state_json(len(chunks), report.pages_total, report.pages_indexed, report, notes))
        plan = self.store.upsert(parsed.source, chunks, self._embed_chunks, state)
        return DocumentResult(
            doc_id=raw.doc_id,
            status=plan.status.value,
            chunks=len(chunks),
            embedded=0 if plan.status is UpsertStatus.UNCHANGED else len(plan.to_embed),
            pages_total=report.pages_total,
            pages_indexed=report.pages_indexed,
            skipped_pages=report.skipped_pages,
            notes=(*report.notes, *notes),
        )

    def _embed_for_split(self, texts: Sequence[str]) -> NDArray[np.float32]:
        with self._boundary_lock:
            return self._embedder.embed_documents_np(texts)

    def _embed_chunks(self, chunks: tuple[Chunk, ...]):
        return self._embedder.embed_documents_np([f"{chunk.header}\n{chunk.text}" for chunk in chunks])

    def delete(self, doc_id: str) -> bool:
        """Delete a document. Call `checkpoint` after a batch of deletes."""
        return self.store.delete_document(doc_id)

    # Retrieval.

    def retrieve(self, query: RetrievalQuery, limits: Limits) -> Retrieved:
        """Run the lexical and dense searches, fuse them with RRF, rerank, and choose with MMR."""
        settings = self.recipe.retrieval
        timer = _Timer("retrieve", limits.retrieve)
        lexical = Fts5Retriever(store=self.store, k=settings.lexical_k, filters=query.filters).search(
            " ".join(query.lexical_terms)
        )
        timer.check()
        timer = _Timer("embed", limits.embed)
        dense = VecRetriever(
            store=self.store, embedder=self._embedder, k=settings.dense_k, filters=query.filters
        ).search(query.dense_text)
        timer.check()
        candidates = self._fuse(lexical, dense)[: settings.rerank_candidates]
        if self._reranker is not None and candidates:
            timer = _Timer("rerank", limits.rerank)
            candidates = self._rerank(candidates, query.rerank_text or query.dense_text)
            timer.check()
        return self._choose(candidates)

    def _fuse(self, lexical: Sequence[Candidate], dense: Sequence[Candidate]) -> list[Candidate]:
        lists = {"lexical": lexical, "dense": dense}
        by_id: dict[str, Chunk] = {}
        for candidate in itertools.chain(lexical, dense):
            by_id.setdefault(candidate.chunk_id, candidate.chunk)
        fused = reciprocal_rank_fusion(
            {name: [c.chunk_id for c in found] for name, found in lists.items()},
            k=self.recipe.retrieval.rrf_k,
        )
        return [
            Candidate(
                by_id[item.chunk_id],
                StageScores(
                    lexical_rank=item.ranks.get("lexical"),
                    dense_rank=item.ranks.get("dense"),
                    rrf_score=item.score,
                ),
            )
            for item in fused
        ]

    def _rerank(self, candidates: list[Candidate], text: str) -> list[Candidate]:
        assert self._reranker is not None
        reranker = OnnxReranker(model=self._reranker, model_id=self.recipe.rag.reranker, top_n=len(candidates))
        return reranker.rerank(candidates, text)

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
        relevance = [final_score(candidates[i]) for i in kept]
        order = mmr_order(relevance, vectors[kept], self.recipe.retrieval.keep, self.recipe.retrieval.mmr_lambda)
        return Retrieved(tuple(candidates[kept[i]] for i in order), duplicates)


def source_kind(value: str) -> SourceKind:
    """Return the source kind for a name. Raise `InvalidRequestError` for an unknown kind."""
    try:
        return SourceKind(value)
    except ValueError as error:
        raise InvalidRequestError("The source kind is unknown.") from error
