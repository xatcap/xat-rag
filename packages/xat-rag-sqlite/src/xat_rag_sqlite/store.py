"""`SqliteRagStore`: one SQLite file for each RAG. See ADR 0005.

The write side (upsert, delete, checkpoint) uses one connection under a lock. Each reading
thread gets its own connection, so queries continue during ingestion (WAL mode).
"""

import contextlib
import json
import sqlite3
import threading
import time
from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

import numpy as np
import sqlite_vec  # pyright: ignore[reportMissingTypeStubs]
from numpy.typing import NDArray
from xat_rag_core.errors import ConfigError, IndexMismatchError, InternalError, ServiceUnavailableError
from xat_rag_core.ids import sha256_hex
from xat_rag_core.lc_codec import span_from_meta, span_to_meta
from xat_rag_core.model import Chunk, Scalar, SourceDocument, SourceKind
from xat_rag_core.query import Equals, ExactLookup, Filter, OneOf, Range

from xat_rag_sqlite.lexical import FTS5_TOKENIZE, index_text
from xat_rag_sqlite.manifest import IndexManifest, check_manifest

_MIN_SQLITE = (3, 44, 0)
_SCHEMA_VERSION = "1"
_MAX_K = 4096
_ID_BATCH = 500
_MAX_READERS = 8
_CHECKPOINT_TRIES = 50

_CHUNK_COLUMNS = (
    "c.id, c.chunk_id, c.doc_id, c.rag, c.source_kind, c.kind, c.header, c.text, c.span, c.content_hash, "
    "c.chunker_version, c.uri, c.title, c.parent_id, c.token_count, c.metadata"
)


class UpsertStatus(StrEnum):
    """The result of an upsert for one document."""

    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class UpsertPlan:
    """What an upsert must do. Only `to_embed` needs new vectors."""

    document: SourceDocument
    status: UpsertStatus
    to_embed: tuple[Chunk, ...]
    to_insert_reused: tuple[tuple[Chunk, bytes], ...]
    to_update: tuple[Chunk, ...]
    stale_ids: tuple[str, ...]
    document_changed: bool = True


def _check_sqlite_version() -> None:
    if sqlite3.sqlite_version_info < _MIN_SQLITE:
        raise ConfigError(
            f"xat-rag needs SQLite 3.44 or later for the FTS5 secure-delete option. "
            f"This Python has SQLite {sqlite3.sqlite_version}. Use the bundled Python of xat-rag."
        )


def _vector_blob(vector: NDArray[np.float32], dims: int) -> bytes:
    array = np.asarray(vector, dtype=np.float32)
    if array.shape != (dims,):
        raise InternalError(f"An embedding must have {dims} dimensions.")
    return array.tobytes()


def _field_rows(chunk: Chunk) -> list[tuple[str, Scalar]]:
    rows: list[tuple[str, Scalar]] = [
        ("doc_id", chunk.doc_id),
        ("source_kind", chunk.source_kind.value),
        ("kind", chunk.kind),
    ]
    rows.extend((name, int(value) if isinstance(value, bool) else value) for name, value in chunk.metadata.items())
    return rows


def _check_k(k: int) -> None:
    if not 1 <= k <= _MAX_K:
        raise InternalError(f"A search needs k between 1 and {_MAX_K}.")


def _document_hash(document: SourceDocument) -> str:
    payload = json.dumps(
        [
            document.source_kind.value,
            document.uri,
            document.title,
            document.version,
            document.content_hash,
            sorted(document.metadata.items()),
        ],
        default=str,
    )
    return sha256_hex(payload.encode("utf-8"))


def _rollback(db: sqlite3.Connection) -> None:
    with contextlib.suppress(sqlite3.Error):  # Keep the first error, not a second one from the rollback.
        db.execute("ROLLBACK")


def _row_hash(chunk: Chunk) -> str:
    payload = json.dumps(
        [
            chunk.rag,
            chunk.source_kind.value,
            chunk.content_hash,
            chunk.kind,
            chunk.parent_id,
            span_to_meta(chunk.span),
            chunk.chunker_version,
            chunk.uri,
            chunk.title,
            chunk.token_count,
            sorted(chunk.metadata.items()),
        ],
        sort_keys=True,
        default=str,
    )
    return sha256_hex(payload.encode("utf-8"))


def _filter_sql(filters: Filter) -> tuple[str, list[object]]:
    """Return an SQL query of matching chunk row IDs, and its parameters. Field names are parameters too."""
    parts: list[str] = []
    params: list[object] = []
    for condition in filters.conditions:
        base = "SELECT chunk_rowid FROM chunk_fields WHERE field = ?"
        match condition:
            case Equals(field=name, value=value):
                parts.append(base + " AND value = ?")
                params += [name, int(value) if isinstance(value, bool) else value]
            case OneOf(field=name, values=values):
                parts.append(base + " AND value IN (" + ",".join("?" * len(values)) + ")")
                params += [name, *(int(v) if isinstance(v, bool) else v for v in values)]
            case Range(field=name, minimum=minimum, maximum=maximum):
                sql = base
                params.append(name)
                if minimum is not None:
                    sql += " AND value >= ?"
                    params.append(minimum)
                if maximum is not None:
                    sql += " AND value <= ?"
                    params.append(maximum)
                parts.append(sql)
    return " INTERSECT ".join(parts), params


class SqliteRagStore:
    """The SQLite file of one RAG."""

    def __init__(self, path: Path, manifest: IndexManifest) -> None:
        """Open or create the file, and check its manifest. Raise `IndexMismatchError` on a mismatch."""
        _check_sqlite_version()
        self._path = path
        self._manifest = manifest
        self._dims = manifest.embedder.dims
        self._write_lock = threading.Lock()
        self._ingest_lock = threading.Lock()
        self._pool_lock = threading.Condition()
        self._idle: list[sqlite3.Connection] = []
        self._open_readers = 0
        self._closed = False
        self._writer = self._connect()
        try:
            self._create_or_check()
        except BaseException:
            self._writer.close()
            raise

    @property
    def manifest(self) -> IndexManifest:
        """The manifest of the index."""
        return self._manifest

    @property
    def path(self) -> Path:
        """The path of the SQLite file."""
        return self._path

    @property
    def open_readers(self) -> int:
        """The number of open reader connections. At most 8."""
        with self._pool_lock:
            return self._open_readers

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, isolation_level=None, check_same_thread=False, timeout=30.0)
        try:
            connection.enable_load_extension(True)
            try:
                sqlite_vec.load(connection)  # pyright: ignore[reportUnknownMemberType]
            finally:
                connection.enable_load_extension(False)
            connection.execute("PRAGMA secure_delete = ON")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
        except BaseException:
            connection.close()
            raise
        return connection

    @contextlib.contextmanager
    def _read(self) -> Generator[sqlite3.Connection]:
        """Borrow a reader connection from a small pool. At most 8 are open at a time."""
        with self._pool_lock:
            while True:
                if self._closed:
                    raise ServiceUnavailableError("The RAG is being rebuilt or stopped. Try again.", 1)
                if self._idle:
                    connection = self._idle.pop()
                    break
                if self._open_readers < _MAX_READERS:
                    self._open_readers += 1
                    connection = None
                    break
                self._pool_lock.wait()
        if connection is None:
            try:
                connection = self._connect()
            except BaseException:
                with self._pool_lock:
                    self._open_readers -= 1
                    self._pool_lock.notify()
                raise
        try:
            yield connection
        finally:
            with self._pool_lock:
                if self._closed:
                    connection.close()
                    self._open_readers -= 1
                else:
                    self._idle.append(connection)
                self._pool_lock.notify()

    def _create_or_check(self) -> None:
        with self._write_lock:
            db = self._writer
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                row = db.execute("SELECT value FROM meta WHERE key = 'manifest'").fetchone()
                version = db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
                if row is not None and (version is None or cast(str, version[0]) != _SCHEMA_VERSION):
                    raise IndexMismatchError(
                        f"The index of the RAG '{self._manifest.rag}' has another schema version. Rebuild the RAG."
                    )
                if row is None:
                    self._create_schema(db)
                    db.execute("INSERT INTO meta(key, value) VALUES ('manifest', ?)", (self._manifest.to_json(),))
                    db.execute("INSERT INTO meta(key, value) VALUES ('schema_version', ?)", (_SCHEMA_VERSION,))
                else:
                    check_manifest(IndexManifest.from_json(cast(str, row[0])), self._manifest)
                db.execute("COMMIT")
            except BaseException:
                _rollback(db)
                raise

    def _create_schema(self, db: sqlite3.Connection) -> None:
        db.execute(
            "CREATE TABLE documents(doc_id TEXT PRIMARY KEY, source_kind TEXT NOT NULL, uri TEXT NOT NULL, "
            "title TEXT NOT NULL, version TEXT NOT NULL, content_hash TEXT NOT NULL, metadata TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE chunks(id INTEGER PRIMARY KEY, chunk_id TEXT NOT NULL UNIQUE, doc_id TEXT NOT NULL "
            "REFERENCES documents(doc_id), rag TEXT NOT NULL, source_kind TEXT NOT NULL, kind TEXT NOT NULL, "
            "header TEXT NOT NULL, text TEXT NOT NULL, span TEXT NOT NULL, content_hash TEXT NOT NULL, "
            "row_hash TEXT NOT NULL, chunker_version TEXT NOT NULL, uri TEXT NOT NULL, title TEXT NOT NULL, "
            "parent_id TEXT, token_count INTEGER, metadata TEXT NOT NULL)"
        )
        db.execute("CREATE INDEX chunks_doc ON chunks(doc_id)")
        db.execute(
            "CREATE TABLE chunk_fields(chunk_rowid INTEGER NOT NULL, field TEXT NOT NULL, value, "
            "PRIMARY KEY (chunk_rowid, field, value)) WITHOUT ROWID"
        )
        db.execute("CREATE INDEX chunk_fields_lookup ON chunk_fields(field, value)")
        db.execute(f'CREATE VIRTUAL TABLE chunks_fts USING fts5(lexical, tokenize="{FTS5_TOKENIZE}")')
        db.execute("INSERT INTO chunks_fts(chunks_fts, rank) VALUES ('secure-delete', 1)")
        db.execute(f"CREATE VIRTUAL TABLE chunks_vec USING vec0(embedding float[{self._dims}] distance_metric=cosine)")

    def close(self) -> None:
        """Close all connections. Readers that are in use close when they are given back."""
        with self._pool_lock:
            self._closed = True
            for connection in self._idle:
                connection.close()
                self._open_readers -= 1
            self._idle.clear()
            self._pool_lock.notify_all()
        with self._write_lock:
            self._writer.close()

    # Write side.

    def upsert(
        self,
        document: SourceDocument,
        chunks: Sequence[Chunk],
        embed: Callable[[tuple[Chunk, ...]], NDArray[np.float32]],
    ) -> UpsertPlan:
        """Plan, embed and write one document. Only one upsert or delete runs at a time on a store.

        Use this method, not `plan_upsert` and `apply_upsert` apart, so that no other write can
        happen between the plan and the write.
        """
        with self._ingest_lock:
            plan = self.plan_upsert(document, chunks)
            vectors = embed(plan.to_embed) if plan.to_embed else np.zeros((0, self._dims), dtype=np.float32)
            self.apply_upsert(plan, vectors)
            return plan

    def plan_upsert(self, document: SourceDocument, chunks: Sequence[Chunk]) -> UpsertPlan:
        """Compare the new chunks with the stored chunks, and return what must change.

        A chunk whose content hash is already stored for this document reuses its vector, so
        unchanged content makes no new embeddings.
        """
        ids = [chunk.chunk_id for chunk in chunks]
        if len(set(ids)) != len(ids):
            raise InternalError("A document has two chunks with the same ID.")
        with self._read() as db:
            db.execute("BEGIN")  # One snapshot for both reads.
            try:
                stored_doc = db.execute(
                    "SELECT source_kind, uri, title, version, content_hash, metadata FROM documents WHERE doc_id = ?",
                    (document.doc_id,),
                ).fetchone()
                rows = db.execute(
                    "SELECT c.chunk_id, c.content_hash, c.row_hash, v.embedding FROM chunks c "
                    "JOIN chunks_vec v ON v.rowid = c.id WHERE c.doc_id = ?",
                    (document.doc_id,),
                ).fetchall()
            finally:
                db.execute("COMMIT")
        stored = {cast(str, r[0]): (cast(str, r[1]), cast(str, r[2])) for r in rows}
        vector_by_hash = {cast(str, r[1]): cast(bytes, r[3]) for r in rows}

        to_embed: list[Chunk] = []
        reused: list[tuple[Chunk, bytes]] = []
        to_update: list[Chunk] = []
        for chunk in chunks:
            old = stored.get(chunk.chunk_id)
            if old is not None and old[0] == chunk.content_hash:
                if old[1] != _row_hash(chunk):
                    to_update.append(chunk)
            elif chunk.content_hash in vector_by_hash:
                reused.append((chunk, vector_by_hash[chunk.content_hash]))
            else:
                to_embed.append(chunk)
        new_ids = set(ids)
        changed_ids = {chunk.chunk_id for chunk in to_embed} | {chunk.chunk_id for chunk, _ in reused}
        stale = tuple(sorted(chunk_id for chunk_id in stored if chunk_id not in new_ids or chunk_id in changed_ids))

        document_changed = stored_doc is None or _document_hash(
            _row_to_document(document, stored_doc)
        ) != _document_hash(document)
        if stored_doc is None:
            status = UpsertStatus.CREATED
        elif not (to_embed or reused or to_update or stale or document_changed):
            status = UpsertStatus.UNCHANGED
        else:
            status = UpsertStatus.UPDATED
        return UpsertPlan(document, status, tuple(to_embed), tuple(reused), tuple(to_update), stale, document_changed)

    def apply_upsert(self, plan: UpsertPlan, vectors: NDArray[np.float32]) -> None:
        """Write a planned upsert in one transaction. `vectors` has one row for each chunk in `to_embed`."""
        if vectors.shape[0] != len(plan.to_embed):
            raise InternalError("The number of vectors does not match the number of chunks to embed.")
        if plan.status is UpsertStatus.UNCHANGED:
            return
        blobs = [_vector_blob(vectors[i], self._dims) for i in range(len(plan.to_embed))]
        document = plan.document
        with self._write_lock:
            db = self._writer
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "INSERT INTO documents(doc_id, source_kind, uri, title, version, content_hash, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(doc_id) DO UPDATE SET "
                    "source_kind = excluded.source_kind, uri = excluded.uri, title = excluded.title, "
                    "version = excluded.version, "
                    "content_hash = excluded.content_hash, metadata = excluded.metadata",
                    (
                        document.doc_id,
                        document.source_kind.value,
                        document.uri,
                        document.title,
                        document.version,
                        document.content_hash,
                        json.dumps(dict(document.metadata), sort_keys=True),
                    ),
                )
                for chunk_id in plan.stale_ids:
                    self._delete_chunk(db, chunk_id)
                for chunk, blob in [*zip(plan.to_embed, blobs, strict=True), *plan.to_insert_reused]:
                    self._insert_chunk(db, chunk, blob)
                for chunk in plan.to_update:
                    self._update_chunk(db, chunk)
                db.execute("COMMIT")
            except BaseException:
                _rollback(db)
                raise

    def _insert_chunk(self, db: sqlite3.Connection, chunk: Chunk, blob: bytes) -> None:
        cursor = db.execute(
            "INSERT INTO chunks(chunk_id, doc_id, rag, source_kind, kind, header, text, span, content_hash, row_hash, "
            "chunker_version, uri, title, parent_id, token_count, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                chunk.chunk_id,
                chunk.doc_id,
                chunk.rag,
                chunk.source_kind.value,
                chunk.kind,
                chunk.header,
                chunk.text,
                json.dumps(span_to_meta(chunk.span)),
                chunk.content_hash,
                _row_hash(chunk),
                chunk.chunker_version,
                chunk.uri,
                chunk.title,
                chunk.parent_id,
                chunk.token_count,
                json.dumps(dict(chunk.metadata), sort_keys=True),
            ),
        )
        rowid = cursor.lastrowid
        db.execute(
            "INSERT INTO chunks_fts(rowid, lexical) VALUES (?, ?)", (rowid, index_text(f"{chunk.header}\n{chunk.text}"))
        )
        db.execute("INSERT INTO chunks_vec(rowid, embedding) VALUES (?, ?)", (rowid, blob))
        db.executemany(
            "INSERT OR IGNORE INTO chunk_fields(chunk_rowid, field, value) VALUES (?, ?, ?)",
            [(rowid, name, value) for name, value in _field_rows(chunk)],
        )

    def _update_chunk(self, db: sqlite3.Connection, chunk: Chunk) -> None:
        row = db.execute("SELECT id FROM chunks WHERE chunk_id = ?", (chunk.chunk_id,)).fetchone()
        if row is None:
            raise InternalError("A chunk to update is not in the store.")
        rowid = cast(int, row[0])
        db.execute(
            "UPDATE chunks SET kind = ?, span = ?, row_hash = ?, chunker_version = ?, uri = ?, title = ?, "
            "parent_id = ?, token_count = ?, metadata = ? WHERE id = ?",
            (
                chunk.kind,
                json.dumps(span_to_meta(chunk.span)),
                _row_hash(chunk),
                chunk.chunker_version,
                chunk.uri,
                chunk.title,
                chunk.parent_id,
                chunk.token_count,
                json.dumps(dict(chunk.metadata), sort_keys=True),
                rowid,
            ),
        )
        db.execute("DELETE FROM chunk_fields WHERE chunk_rowid = ?", (rowid,))
        db.executemany(
            "INSERT OR IGNORE INTO chunk_fields(chunk_rowid, field, value) VALUES (?, ?, ?)",
            [(rowid, name, value) for name, value in _field_rows(chunk)],
        )

    def _delete_chunk(self, db: sqlite3.Connection, chunk_id: str) -> None:
        row = db.execute("SELECT id FROM chunks WHERE chunk_id = ?", (chunk_id,)).fetchone()
        if row is None:
            return
        rowid = cast(int, row[0])
        db.execute("DELETE FROM chunks_fts WHERE rowid = ?", (rowid,))
        db.execute("DELETE FROM chunks_vec WHERE rowid = ?", (rowid,))
        db.execute("DELETE FROM chunk_fields WHERE chunk_rowid = ?", (rowid,))
        db.execute("DELETE FROM chunks WHERE id = ?", (rowid,))

    def delete_document(self, doc_id: str) -> bool:
        """Delete a document and all its chunks. Return `False` if the document is not in the store.

        Call `checkpoint()` after a batch of deletes, so that the WAL file keeps no deleted text.
        """
        with self._ingest_lock, self._write_lock:
            db = self._writer
            db.execute("BEGIN IMMEDIATE")
            try:
                ids = [cast(str, r[0]) for r in db.execute("SELECT chunk_id FROM chunks WHERE doc_id = ?", (doc_id,))]
                for chunk_id in ids:
                    self._delete_chunk(db, chunk_id)
                deleted = db.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,)).rowcount > 0
                db.execute("COMMIT")
            except BaseException:
                _rollback(db)
                raise
        return deleted

    def checkpoint(self) -> None:
        """Write the WAL into the main file and truncate it, so that the WAL keeps no deleted pages.

        A reader with an open snapshot blocks the checkpoint. The method tries again for about
        five seconds, then raises `InternalError`. It never ends in silence with deleted text in the WAL.
        """
        for _ in range(_CHECKPOINT_TRIES):
            with self._write_lock:
                row = self._writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if row is not None and int(row[0]) == 0:
                return
            time.sleep(0.1)
        raise InternalError("The WAL checkpoint was blocked by readers. Deleted text can still be in the WAL file.")

    # Read side.

    def _query_chunks(self, sql: str, params: Sequence[object]) -> list[Chunk]:
        with self._read() as db:
            return [_row_to_chunk(row) for row in db.execute(sql, params).fetchall()]

    def counts(self) -> tuple[int, int]:
        """Return the number of documents and the number of chunks."""
        with self._read() as db:
            documents = cast(int, db.execute("SELECT count(*) FROM documents").fetchone()[0])
            chunks = cast(int, db.execute("SELECT count(*) FROM chunks").fetchone()[0])
        return documents, chunks

    def has_document(self, doc_id: str) -> bool:
        """Return `True` if the document is in the store."""
        with self._read() as db:
            return db.execute("SELECT 1 FROM documents WHERE doc_id = ?", (doc_id,)).fetchone() is not None

    def chunk_exists(self, chunk_id: str) -> bool:
        """Return `True` if the chunk is in the store."""
        with self._read() as db:
            return db.execute("SELECT 1 FROM chunks WHERE chunk_id = ?", (chunk_id,)).fetchone() is not None

    def get_chunks(self, chunk_ids: Iterable[str]) -> dict[str, Chunk]:
        """Return the stored chunks for the given IDs. Unknown IDs are left out."""
        ids = list(dict.fromkeys(chunk_ids))
        result: dict[str, Chunk] = {}
        with self._read() as db:
            for offset in range(0, len(ids), _ID_BATCH):
                batch = ids[offset : offset + _ID_BATCH]
                sql = f"SELECT {_CHUNK_COLUMNS} FROM chunks c WHERE c.chunk_id IN ({','.join('?' * len(batch))})"
                for row in db.execute(sql, batch).fetchall():
                    chunk = _row_to_chunk(row)
                    result[chunk.chunk_id] = chunk
        return result

    def vectors(self, chunk_ids: Sequence[str]) -> NDArray[np.float32]:
        """Return the stored vectors of the chunks, one row for each ID, in order."""
        if not chunk_ids:
            return np.zeros((0, self._dims), dtype=np.float32)
        ids = list(dict.fromkeys(chunk_ids))
        by_id: dict[str, NDArray[np.float32]] = {}
        with self._read() as db:
            for offset in range(0, len(ids), _ID_BATCH):
                batch = ids[offset : offset + _ID_BATCH]
                sql = (
                    "SELECT c.chunk_id, v.embedding FROM chunks c JOIN chunks_vec v ON v.rowid = c.id "
                    f"WHERE c.chunk_id IN ({','.join('?' * len(batch))})"
                )
                for row in db.execute(sql, batch).fetchall():
                    by_id[cast(str, row[0])] = np.frombuffer(cast(bytes, row[1]), dtype=np.float32)
        missing = [chunk_id for chunk_id in chunk_ids if chunk_id not in by_id]
        if missing:
            raise InternalError("A vector of a chunk is not in the store.")
        return np.stack([by_id[chunk_id] for chunk_id in chunk_ids])

    def vectors_found(self, chunk_ids: Sequence[str]) -> dict[str, NDArray[np.float32]]:
        """Return the stored vectors of the chunks that still exist, by chunk ID."""
        ids = list(dict.fromkeys(chunk_ids))
        found: dict[str, NDArray[np.float32]] = {}
        with self._read() as db:
            for offset in range(0, len(ids), _ID_BATCH):
                batch = ids[offset : offset + _ID_BATCH]
                sql = (
                    "SELECT c.chunk_id, v.embedding FROM chunks c JOIN chunks_vec v ON v.rowid = c.id "
                    f"WHERE c.chunk_id IN ({','.join('?' * len(batch))})"
                )
                for row in db.execute(sql, batch).fetchall():
                    found[cast(str, row[0])] = np.frombuffer(cast(bytes, row[1]), dtype=np.float32)
        return found

    def search_lexical(self, expression: str, k: int, filters: Filter) -> list[Chunk]:
        """Return the best `k` chunks for an FTS5 `MATCH` expression, by BM25, then by chunk ID."""
        _check_k(k)
        filter_sql, filter_params = _filter_sql(filters)
        sql = (
            f"SELECT {_CHUNK_COLUMNS} FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid WHERE chunks_fts MATCH ?"
        )
        params: list[object] = [expression]
        if filter_sql:
            sql += f" AND chunks_fts.rowid IN ({filter_sql})"
            params += filter_params
        sql += " ORDER BY bm25(chunks_fts), c.chunk_id LIMIT ?"
        params.append(k)
        return self._query_chunks(sql, params)

    def search_vector(self, vector: NDArray[np.float32], k: int, filters: Filter) -> list[Chunk]:
        """Return the `k` nearest chunks by cosine distance, then by chunk ID. Filters apply before the cut."""
        _check_k(k)
        filter_sql, filter_params = _filter_sql(filters)
        inner = "SELECT rowid, distance FROM chunks_vec WHERE embedding MATCH ? AND k = ?"
        params: list[object] = [_vector_blob(vector, self._dims), k]
        if filter_sql:
            inner += f" AND rowid IN ({filter_sql})"
            params += filter_params
        sql = (
            f"SELECT {_CHUNK_COLUMNS} FROM ({inner}) v JOIN chunks c ON c.id = v.rowid ORDER BY v.distance, c.chunk_id"
        )
        return self._query_chunks(sql, params)

    def exact_lookup(self, lookups: Sequence[ExactLookup], k: int, filters: Filter) -> list[Chunk]:
        """Return chunks whose metadata equals a lookup, by the number of matched lookups, then by chunk ID."""
        _check_k(k)
        if not lookups:
            return []
        where = " OR ".join("(f.field = ? AND f.value = ?)" for _ in lookups)
        params: list[object] = [part for lookup in lookups for part in (lookup.field, lookup.value)]
        sql = (
            f"SELECT {_CHUNK_COLUMNS}, count(*) AS hits FROM chunk_fields f JOIN chunks c ON c.id = f.chunk_rowid "
            f"WHERE ({where})"
        )
        filter_sql, filter_params = _filter_sql(filters)
        if filter_sql:
            sql += f" AND c.id IN ({filter_sql})"
            params += filter_params
        sql += " GROUP BY c.id ORDER BY hits DESC, c.chunk_id LIMIT ?"
        params.append(k)
        return self._query_chunks(sql, params)


def _row_to_document(document: SourceDocument, row: Sequence[Any]) -> SourceDocument:
    """Return the stored document fields as a `SourceDocument`, for comparison."""
    return SourceDocument(
        doc_id=document.doc_id,
        rag=document.rag,
        source_kind=SourceKind(cast(str, row[0])),
        uri=cast(str, row[1]),
        title=cast(str, row[2]),
        version=cast(str, row[3]),
        content_hash=cast(str, row[4]),
        metadata=cast(dict[str, Scalar], json.loads(cast(str, row[5]))),
    )


def _row_to_chunk(row: Sequence[Any]) -> Chunk:
    metadata = cast(Mapping[str, Scalar], json.loads(cast(str, row[15])))
    return Chunk(
        chunk_id=cast(str, row[1]),
        doc_id=cast(str, row[2]),
        rag=cast(str, row[3]),
        source_kind=SourceKind(cast(str, row[4])),
        kind=cast(str, row[5]),
        header=cast(str, row[6]),
        text=cast(str, row[7]),
        span=span_from_meta(json.loads(cast(str, row[8]))),
        content_hash=cast(str, row[9]),
        chunker_version=cast(str, row[10]),
        uri=cast(str, row[11]),
        title=cast(str, row[12]),
        parent_id=cast(str | None, row[13]),
        token_count=cast(int | None, row[14]),
        metadata=dict(metadata),
    )
