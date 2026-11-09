"""Ingestion jobs. Each RAG has one worker, so its jobs run in order and other RAGs are not blocked.

A job can be cancelled between documents. A watchdog thread marks the service degraded when one
document runs much longer than the parse deadline, because a Python thread cannot be stopped (ADR 0012).
"""

import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum

from xat_rag.types import DocumentResult
from xat_rag_core.errors import (
    InvalidRequestError,
    NotFoundError,
    OverloadedError,
    ServiceUnavailableError,
    XatRagError,
)
from xat_rag_core.model import RawDocument

_KEPT_FINISHED_JOBS = 200
_MAX_QUEUED_BYTES = 256 * 1024 * 1024
_WATCH_SECONDS = 2.0


class JobStatus(StrEnum):
    """The state of a job."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"


_FINISHED = frozenset({JobStatus.DONE, JobStatus.CANCELLED, JobStatus.FAILED})


@dataclass(slots=True)
class Job:
    """One ingestion job."""

    job_id: str
    rag: str
    total: int
    size: int
    status: JobStatus = JobStatus.QUEUED
    results: list[DocumentResult] = field(default_factory=list[DocumentResult])
    error_type: str | None = None
    notes: list[str] = field(default_factory=list[str])
    cancel: threading.Event = field(default_factory=threading.Event)
    document_started: float | None = None


class JobManager:
    """Runs ingestion jobs, one worker for each RAG, and watches for a parse that does not stop."""

    def __init__(
        self,
        ingest: Callable[[RawDocument], DocumentResult],
        checkpoint: Callable[[str], None],
        stuck_after_seconds: float,
    ) -> None:
        """Make a manager. A document that runs longer than `stuck_after_seconds` makes the service degraded."""
        self._ingest = ingest
        self._checkpoint = checkpoint
        self._stuck_after = stuck_after_seconds
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()
        self._workers: dict[str, ThreadPoolExecutor] = {}
        self._queued_bytes = 0
        self._stopping = threading.Event()
        self.degraded_reason: str | None = None
        threading.Thread(target=self._watch, name="xat-rag-watchdog", daemon=True).start()

    def submit(self, rag: str, documents: Sequence[RawDocument]) -> Job:
        """Start a job. Raise `ServiceUnavailableError` if degraded, `OverloadedError` if too much data waits."""
        self.check_degraded()
        size = sum(len(document.data) for document in documents)
        job = Job(job_id=f"j_{uuid.uuid4().hex[:20]}", rag=rag, total=len(documents), size=size)
        with self._lock:
            if self._queued_bytes + size > _MAX_QUEUED_BYTES:
                raise OverloadedError("Too much uploaded data waits for ingestion. Try again later.", 5)
            self._queued_bytes += size
            self._jobs[job.job_id] = job
            self._evict()
            worker = self._workers.get(rag)
            if worker is None:
                worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"xat-rag-ingest-{len(self._workers)}")
                self._workers[rag] = worker
        worker.submit(self._run, job, list(documents))
        return job

    def _evict(self) -> None:
        finished = [job_id for job_id, job in self._jobs.items() if job.status in _FINISHED]
        for job_id in finished[: max(0, len(finished) - _KEPT_FINISHED_JOBS)]:
            del self._jobs[job_id]

    def _run(self, job: Job, documents: list[RawDocument]) -> None:
        job.status = JobStatus.RUNNING
        try:
            while documents:
                if job.cancel.is_set() or self._stopping.is_set():
                    job.status = JobStatus.CANCELLED
                    return
                document = documents.pop(0)  # Drop each document after use, to free its bytes.
                job.document_started = time.monotonic()
                try:
                    job.results.append(self._ingest(document))
                except XatRagError as error:  # One bad document does not stop the others (ADR 0009).
                    job.results.append(
                        DocumentResult(
                            document.doc_id,
                            "failed",
                            error_type=error.error_type.value,
                            message="The document could not be ingested.",
                        )
                    )
                job.document_started = None
            try:
                self._checkpoint(job.rag)
            except Exception as error:  # The documents are in. Only the WAL cleanup failed.
                job.notes.append(f"checkpoint_failed:{getattr(error, 'error_type', 'internal_error')}")
            job.status = JobStatus.DONE
        except Exception as error:  # A failed job is reported with its error type. Logs hold no content.
            job.status = JobStatus.FAILED
            job.error_type = str(getattr(error, "error_type", "internal_error"))
        finally:
            job.document_started = None
            with self._lock:
                self._queued_bytes -= job.size

    def get(self, job_id: str) -> Job:
        """Return a job. Raise `NotFoundError` if it does not exist."""
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise NotFoundError("The job does not exist.")
        return job

    def cancel(self, job_id: str) -> Job:
        """Ask a job to stop before its next document."""
        job = self.get(job_id)
        job.cancel.set()
        return job

    def has_active(self, rag: str) -> bool:
        """Return `True` if a job of the RAG is queued or running."""
        with self._lock:
            return any(job.rag == rag and job.status not in _FINISHED for job in self._jobs.values())

    def require_idle(self, rag: str) -> None:
        """Raise `InvalidRequestError` if a job of the RAG is queued or running."""
        if self.has_active(rag):
            raise InvalidRequestError("An ingestion job of this RAG is running. Wait for it, or cancel it.")

    def _watch(self) -> None:
        while not self._stopping.wait(_WATCH_SECONDS):
            now = time.monotonic()
            with self._lock:
                jobs = list(self._jobs.values())
            if any(job.document_started is not None and now - job.document_started > self._stuck_after for job in jobs):
                self.degraded_reason = "parse_timeout"

    def check_degraded(self) -> None:
        """Raise `ServiceUnavailableError` if the service is degraded."""
        if self.degraded_reason is not None:
            raise ServiceUnavailableError(
                "A document did not finish in time. Restart the xat-rag service.", retry_after_seconds=None
            )

    def shutdown(self, timeout_seconds: float = 30.0) -> None:
        """Cancel the jobs, and wait up to `timeout_seconds` for the running documents to finish."""
        self._stopping.set()
        with self._lock:
            workers = list(self._workers.values())
        deadline = time.monotonic() + timeout_seconds
        for worker in workers:
            worker.shutdown(wait=False, cancel_futures=True)
        for job in list(self._jobs.values()):
            while job.document_started is not None and time.monotonic() < deadline:
                time.sleep(0.05)
