"""The FastAPI app of the xat-rag service: routes, access control, limits and the error model.

See the architecture, sections 9 and 11, and ADRs 0003 and 0009.
"""

import asyncio
import hmac
import logging
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from xat_rag import ContextRequest, XatRag
from xat_rag_core.errors import (
    AuthenticationError,
    ErrorType,
    InvalidRequestError,
    NotFoundError,
    OverloadedError,
    ServiceUnavailableError,
    XatRagError,
)
from xat_rag_core.query import MAX_QUESTION_CHARS, RetrievalQuery
from xat_rag_core.query import build_query as make_query
from xat_rag_core.ranking import MAX_CHUNKS
from xat_rag_core.tracing import untraced

from xat_rag_service.jobs import Job, JobManager
from xat_rag_service.serialize import chunk_json, document_result_json, query_json, result_json
from xat_rag_service.uploads import raw_documents, read_upload

MIB = 1024 * 1024
MAX_UPLOAD_BYTES = 64 * MIB
MAX_JSON_BYTES = 1 * MIB
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})
_INTERNAL_MESSAGE = "An unexpected failure happened in xat-rag."
LOG = logging.getLogger("xat_rag")
_OPEN_PATHS = frozenset({"/health/live"})


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContextBody(_Body):
    """The body of `POST /v1/context`."""

    context: str = Field(min_length=1, max_length=64)
    question: str = Field(max_length=MAX_QUESTION_CHARS)
    chunks: int | None = Field(default=None, ge=1, le=MAX_CHUNKS, strict=True)
    debug: bool = False


class QueryBody(_Body):
    """The body of `POST /v1/query/build` and `POST /v1/rags/{rag}/search`."""

    question: str = Field(default="", max_length=MAX_QUESTION_CHARS)


def error_response(error: XatRagError) -> JSONResponse:
    """Return the error body for an error. An internal error has no details (ADR 0009)."""
    if error.error_type is ErrorType.INTERNAL:
        LOG.error("internal error: %s", type(error).__name__)
        return _plain_error(500, ErrorType.INTERNAL, _INTERNAL_MESSAGE)
    headers: dict[str, str] = {}
    retry = getattr(error, "retry_after_seconds", None)
    if isinstance(error, ServiceUnavailableError | OverloadedError) and retry is not None:
        headers["Retry-After"] = str(retry)
    return JSONResponse({"error": {"message": error.message, "type": error.error_type.value}}, error.status, headers)


def _plain_error(status: int, error_type: ErrorType, message: str) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": error_type.value}}, status)


class Limiter:
    """Runs blocking work in a bounded pool, with at most `queue_limit` requests waiting or running."""

    def __init__(self, queue_limit: int, workers: int = 2) -> None:
        """Make the limiter."""
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="xat-rag-query")
        self._limit = queue_limit
        self._active = 0
        self._lock = threading.Lock()

    async def run[R](self, function: Callable[..., R], *args: Any) -> R:
        """Run `function` with tracing off in a worker thread. Raise `OverloadedError` if the queue is full."""
        with self._lock:
            if self._active >= self._limit:
                raise OverloadedError("Too many requests are waiting. Try again soon.", retry_after_seconds=1)
            self._active += 1
        try:
            future = self._executor.submit(untraced, function, *args)
            return await asyncio.wrap_future(future)
        finally:
            with self._lock:
                self._active -= 1

    def shutdown(self) -> None:
        """Stop the workers."""
        self._executor.shutdown(wait=False, cancel_futures=True)


def _query(body: QueryBody) -> RetrievalQuery:
    return make_query(body.question)


def _job_json(job: Job) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "rag": job.rag,
        "status": job.status.value,
        "total": job.total,
        "done": len(job.results),
        "error_type": job.error_type,
        "notes": list(job.notes),
        "results": [document_result_json(result) for result in job.results],
    }


async def _read_body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise _TooLargeError
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise _TooLargeError
        chunks.append(chunk)
    return b"".join(chunks)


def _host_name(header: str) -> str:
    """Return the host part of a Host header, without the port, in lowercase."""
    header = header.lower()
    if header.startswith("["):
        return header.split("]", 1)[0] + "]"
    return header.rsplit(":", 1)[0] if ":" in header else header


def _field_names(errors: Any) -> str:
    """Return the names of the fields with errors. A field that the model does not know is not named."""
    names: set[str] = set()
    for item in cast(list[dict[str, Any]], errors):
        if item.get("type") == "extra_forbidden":
            names.add("(an unknown field)")
        else:
            names.add(".".join(str(part) for part in item.get("loc", ()) if part != "body"))
    return ", ".join(sorted(names)) or "the JSON body"


class _TooLargeError(Exception):
    """The request body is larger than its limit."""


def create_app(rag: XatRag, access_key: str, queue_limit: int, version: str) -> FastAPI:  # noqa: PLR0915
    """Make the app. `access_key` must be the key that the app gave at start."""
    if len(access_key) < 16:
        raise InvalidRequestError("The access key must have at least 16 characters.")
    app = FastAPI(title="xat-rag", version=version, docs_url=None, redoc_url=None)
    limiter = Limiter(queue_limit)
    # Embedding and storing come after the parse, so a stuck document is one that takes far longer.
    stuck_after = rag.settings.parse_deadline_seconds * 2 + 300
    jobs = JobManager(rag.ingest_one, rag.checkpoint, stuck_after_seconds=stuck_after)
    app.state.limiter = limiter
    app.state.jobs = jobs
    expected = f"Bearer {access_key}".encode()

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if _host_name(request.headers.get("host") or "") not in _LOOPBACK_HOSTS:
            return _plain_error(400, ErrorType.INVALID_REQUEST, "The Host header must be a loopback name.")
        if "origin" in request.headers:
            return _plain_error(400, ErrorType.INVALID_REQUEST, "Requests from browsers are refused.")
        given = (request.headers.get("authorization") or "").encode("latin-1", "replace")
        if request.url.path not in _OPEN_PATHS and not hmac.compare_digest(given, expected):
            return error_response(AuthenticationError("The access key is missing or wrong."))
        try:
            return await call_next(request)
        except Exception as error:  # Only the type goes into the log, never the message (ADR 0010).
            LOG.error("unexpected error: %s", type(error).__name__)
            return _plain_error(500, ErrorType.INTERNAL, _INTERNAL_MESSAGE)

    @app.exception_handler(XatRagError)
    async def on_error(_: Request, error: XatRagError) -> JSONResponse:
        return error_response(error)

    @app.exception_handler(RequestValidationError)
    async def on_validation(_: Request, error: RequestValidationError) -> JSONResponse:
        # The default message can hold the input value. This one names only the fields.
        return _plain_error(
            400, ErrorType.INVALID_REQUEST, f"The request is not valid. Check: {_field_names(error.errors())}."
        )

    @app.exception_handler(StarletteHTTPException)
    async def on_http(_: Request, error: StarletteHTTPException) -> JSONResponse:
        if error.status_code == 404:
            return _plain_error(404, ErrorType.NOT_FOUND, "The route does not exist.")
        return _plain_error(error.status_code, ErrorType.INVALID_REQUEST, "The request is not valid for this route.")

    @app.exception_handler(_TooLargeError)
    async def on_too_large(_: Request, __: _TooLargeError) -> JSONResponse:
        return _plain_error(413, ErrorType.INVALID_REQUEST, "The request body is too large.")

    async def json_body[M: BaseModel](request: Request, model: type[M]) -> M:
        body = await _read_body(request, MAX_JSON_BYTES)
        try:
            return model.model_validate_json(body)
        except ValidationError as error:
            # The default message can hold the input value. This one names only the fields.
            raise InvalidRequestError(f"The request is not valid. Check: {_field_names(error.errors())}.") from None

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    async def ready() -> JSONResponse:
        try:
            jobs.check_degraded()
        except ServiceUnavailableError:
            return JSONResponse({"status": "degraded", "reason": jobs.degraded_reason}, 503)
        return JSONResponse({"status": "ready", "version": version, "needs_rebuild": sorted(rag.needs_rebuild())})

    @app.get("/v1/rags")
    async def rags() -> dict[str, Any]:
        mismatched = rag.needs_rebuild()
        items: list[dict[str, Any]] = []
        for name in rag.rag_names():
            if name in mismatched:
                items.append({"name": name, "needs_rebuild": True})
                continue
            documents, chunks = await limiter.run(rag.index(name).store.counts)
            items.append({"name": name, "needs_rebuild": False, "documents": documents, "chunks": chunks})
        return {"rags": items, "contexts": rag.context_names()}

    @app.get("/v1/rags/{name}")
    async def rag_detail(name: str) -> dict[str, Any]:
        index = rag.index(name)
        documents, chunks = await limiter.run(index.store.counts)
        manifest = index.store.manifest
        return {
            "name": name,
            "documents": documents,
            "chunks": chunks,
            "manifest": {
                "index_format": manifest.index_format,
                "embedder": manifest.embedder.model_id,
                "embedder_sha256": manifest.embedder.sha256,
                "dims": manifest.embedder.dims,
                "lexical_tokenizer": manifest.lexical_tokenizer,
                "size_tokenizer_id": manifest.size_tokenizer_id,
                "index_hash": manifest.index_hash,
                "chunkers": dict(manifest.chunkers),
            },
        }

    @app.put("/v1/rags/{name}/documents", status_code=202)
    async def put_documents(name: str, request: Request) -> dict[str, Any]:
        index = rag.index(name)
        jobs.check_degraded()
        body = await _read_body(request, MAX_UPLOAD_BYTES)
        upload = await limiter.run(read_upload, request.headers.get("content-type", ""), body)
        del body
        documents = raw_documents(upload, name)
        index.check_kinds({document.source_kind.value for document in documents})
        job = jobs.submit(name, documents)
        return {"job_id": job.job_id}

    @app.get("/v1/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, Any]:
        return _job_json(jobs.get(job_id))

    @app.delete("/v1/jobs/{job_id}")
    async def cancel_job(job_id: str) -> dict[str, Any]:
        return _job_json(jobs.cancel(job_id))

    @app.delete("/v1/rags/{name}/documents/{doc_id:path}")
    async def delete_document(name: str, doc_id: str) -> dict[str, Any]:
        deleted = await limiter.run(rag.delete, name, doc_id)
        if not deleted:
            raise NotFoundError("The document is not in the RAG.")
        return {"deleted": True}

    @app.post("/v1/rags/{name}/rebuild")
    async def rebuild(name: str) -> dict[str, Any]:
        jobs.require_idle(name)
        await limiter.run(rag.rebuild, name)
        return {"rebuilt": True, "documents": 0}

    @app.post("/v1/rags/{name}/search")
    async def search(name: str, request: Request) -> dict[str, Any]:
        body = await json_body(request, QueryBody)
        query = await limiter.run(_query, body)
        hits = await limiter.run(rag.search_hits, name, query)
        return {
            "query": query_json(query),
            "hits": [
                {
                    **chunk_json(hit.chunk),
                    "lexical_rank": hit.scores.lexical_rank,
                    "dense_rank": hit.scores.dense_rank,
                    "rrf_score": hit.scores.rrf_score,
                    "rerank_score": hit.scores.rerank_score,
                }
                for hit in hits
            ],
        }

    @app.post("/v1/context")
    async def context(request: Request) -> dict[str, Any]:
        body = await json_body(request, ContextBody)
        context_request = ContextRequest(body.context, body.question, body.chunks, body.debug)
        return result_json(await limiter.run(rag.retrieve, context_request))

    @app.post("/v1/query/build")
    async def build_query(request: Request) -> dict[str, Any]:
        body = await json_body(request, QueryBody)
        return query_json(await limiter.run(_query, body))

    return app
