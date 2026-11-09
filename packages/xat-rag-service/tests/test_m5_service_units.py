"""Milestone 5 unit tests of the service: settings, upload parsing, jobs, the limiter and helpers. No models needed."""

import asyncio
import dataclasses
import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from xat_rag.types import ChunkSource, DebugCandidate, DocumentResult, OmittedItem, RetrievalResult, RetrievedChunk
from xat_rag_core.errors import (
    ConfigError,
    InternalError,
    InvalidRequestError,
    NotFoundError,
    OverloadedError,
    ServiceUnavailableError,
    StageError,
    XatRagError,
)
from xat_rag_core.model import BlockSpan, LineSpan, PageSpan, RawDocument, SkippedPage, SourceKind
from xat_rag_core.query import RetrievalQuery
from xat_rag_service.app import Limiter, error_response
from xat_rag_service.jobs import Job, JobManager, JobStatus
from xat_rag_service.run import lock_data_folder, remove_ready_file, write_ready_file
from xat_rag_service.serialize import chunk_json, document_result_json, query_json, result_json, span_json
from xat_rag_service.settings import Listen, parse_listen, read_settings
from xat_rag_service.uploads import MAX_DOCUMENTS, DocumentFields, Upload, raw_documents, read_upload

# Settings.

REQUIRED = {
    "XatRag__DataFolder": "/data",
    "XatRag__RecipesFolder": "/recipes",
    "XatRag__ModelsFolder": "/models",
    "XatRag__LockFile": "/models.lock.json",
}


def test_settings_defaults() -> None:
    settings = read_settings(REQUIRED)
    assert settings.listen == Listen("127.0.0.1", 0, None)
    assert (settings.queue_limit, settings.threads) == (8, 0)
    assert not hasattr(settings, "prompts_folder")
    assert settings.ready_file is None
    assert settings.stop_when_input_closes is False
    assert settings.data_folder == Path("/data")


def test_settings_names_are_compared_without_case() -> None:
    environ = {key.lower(): value for key, value in REQUIRED.items()}
    environ["XATRAG__QUEUELIMIT"] = "3"
    environ["xatrag__readyfile"] = "/tmp/ready.json"
    settings = read_settings(environ)
    assert settings.queue_limit == 3
    assert settings.ready_file == Path("/tmp/ready.json")


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_each_required_setting_is_checked(missing: str) -> None:
    environ = {key: value for key, value in REQUIRED.items() if key != missing}
    with pytest.raises(ConfigError, match=f"Set {missing}\\."):
        read_settings(environ)
    with pytest.raises(ConfigError, match=f"Set {missing}\\."):
        read_settings({**environ, missing: ""})


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("QueueLimit", "abc"),
        ("QueueLimit", "1.5"),
        ("QueueLimit", "0"),
        ("QueueLimit", "-1"),
        ("QueueLimit", "1001"),
        ("Threads", "abc"),
        ("Threads", "-1"),
        ("Threads", "257"),
    ],
)
def test_bad_numbers_are_config_errors(name: str, value: str) -> None:
    with pytest.raises(ConfigError, match="QueueLimit"):
        read_settings({**REQUIRED, f"XatRag__{name}": value})


@pytest.mark.parametrize(
    ("name", "value"), [("QueueLimit", "1"), ("QueueLimit", "1000"), ("Threads", "0"), ("Threads", "256")]
)
def test_number_limits_are_inclusive(name: str, value: str) -> None:
    settings = read_settings({**REQUIRED, f"XatRag__{name}": value})
    assert str(getattr(settings, "queue_limit" if name == "QueueLimit" else "threads")) == value


@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), ("TRUE", True), ("True", True), ("false", False), ("1", False), ("yes", False), ("", False)],
)
def test_stop_when_input_closes_is_true_only_for_the_word_true(value: str, expected: bool) -> None:
    assert read_settings({**REQUIRED, "XatRag__StopWhenInputCloses": value}).stop_when_input_closes is expected


def test_the_prompts_folder_setting_is_gone() -> None:
    settings = read_settings({**REQUIRED, "XatRag__PromptsFolder": "/p"})
    assert not hasattr(settings, "prompts_folder")
    assert "prompts_folder" not in {field.name for field in dataclasses.fields(settings)}


# The listen setting.


def test_unix_socket_urls() -> None:
    assert parse_listen("http://unix:/tmp/xat-rag.sock") == Listen(None, None, "/tmp/xat-rag.sock")
    assert parse_listen("http://unix:relative.sock") == Listen(None, None, "relative.sock")
    assert read_settings({**REQUIRED, "XatRag__Urls": "http://unix:/tmp/x.sock"}).listen.unix_path == "/tmp/x.sock"
    with pytest.raises(ConfigError, match="socket path"):
        parse_listen("http://unix:")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:5000", Listen("127.0.0.1", 5000, None)),
        ("http://127.0.0.1", Listen("127.0.0.1", 0, None)),
        ("http://127.0.0.1:0", Listen("127.0.0.1", 0, None)),
        ("http://localhost:5000", Listen("127.0.0.1", 5000, None)),
        ("http://localhost", Listen("127.0.0.1", 0, None)),
        ("http://[::1]:7000", Listen("::1", 7000, None)),
        ("HTTP://127.0.0.1:81", Listen("127.0.0.1", 81, None)),
    ],
)
def test_loopback_urls(url: str, expected: Listen) -> None:
    assert parse_listen(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "127.0.0.1:80",
        "http://0.0.0.0:80",
        "http://192.168.1.2:80",
        "http://[::]:80",
        "http://example.com",
        "http://127.0.0.1.evil.com:80",
        "http://localhost.evil.com",
        "http://evil.com/127.0.0.1",
        "https://127.0.0.1:443",
        "ftp://127.0.0.1",
        "unix:/tmp/x.sock",
        "http://unix/tmp/x.sock",
    ],
)
def test_non_loopback_urls_are_refused(url: str) -> None:
    with pytest.raises(ConfigError, match="XatRag__Urls"):
        parse_listen(url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:99999", "http://127.0.0.1:abc", "http://127.0.0.1:-1"])
def test_a_bad_port_is_a_config_error(url: str) -> None:
    with pytest.raises(ConfigError, match="XatRag__Urls"):
        parse_listen(url)


# Upload parsing.

BOUNDARY = "xat-m5-boundary"
CONTENT_TYPE = f"multipart/form-data; boundary={BOUNDARY}"


def part(name: str, data: bytes, filename: str | None = None, extra_headers: str = "") -> bytes:
    disposition = f'Content-Disposition: form-data; name="{name}"' + (f'; filename="{filename}"' if filename else "")
    return f"--{BOUNDARY}\r\n{disposition}\r\n{extra_headers}\r\n".encode() + data + b"\r\n"


def body(*parts: bytes) -> bytes:
    return b"".join(parts) + f"--{BOUNDARY}--\r\n".encode()


def fields_json(count: int = 1, part_name: str = "f") -> bytes:
    return json.dumps([{"doc_id": f"d{i}", "kind": "text", "part": part_name} for i in range(count)]).encode()


def test_a_valid_upload_is_read() -> None:
    upload = read_upload(CONTENT_TYPE, body(part("documents", fields_json()), part("f", b"hello", "f.txt")))
    assert upload.files == {"f": b"hello"}
    assert [f.doc_id for f in upload.fields] == ["d0"]


def test_file_bytes_are_kept_exactly() -> None:
    data = b"line1\r\nline2\r\n\x00\xff\xfe\r\n--another-boundary\r\n--\r\n\r\n\r\n"
    upload = read_upload(CONTENT_TYPE, body(part("documents", fields_json()), part("f", data, "f.bin")))
    assert upload.files["f"] == data


def test_the_content_type_is_case_insensitive() -> None:
    content_type = CONTENT_TYPE.replace("multipart/form-data", "Multipart/Form-Data")
    assert read_upload(content_type, body(part("documents", fields_json()), part("f", b"x"))).files == {"f": b"x"}


@pytest.mark.parametrize(
    "content_type", ["", "application/json", "text/plain", "multipart/mixed; boundary=x", "form-data; boundary=x"]
)
def test_a_wrong_content_type_is_refused(content_type: str) -> None:
    with pytest.raises(InvalidRequestError, match="multipart/form-data"):
        read_upload(content_type, b"{}")


def test_a_missing_documents_part_is_refused() -> None:
    with pytest.raises(InvalidRequestError, match="needs a 'documents' part"):
        read_upload(CONTENT_TYPE, body(part("f", b"x")))
    with pytest.raises(InvalidRequestError):
        read_upload(CONTENT_TYPE, b"")


def test_a_document_that_names_an_unknown_part_is_refused() -> None:
    with pytest.raises(InvalidRequestError, match="names a file part that is not in the upload"):
        read_upload(CONTENT_TYPE, body(part("documents", fields_json(part_name="nope")), part("f", b"x")))


def test_a_duplicate_or_unnamed_part_is_refused() -> None:
    with pytest.raises(InvalidRequestError, match="unique name"):
        read_upload(CONTENT_TYPE, body(part("documents", fields_json()), part("f", b"1"), part("f", b"2")))
    unnamed = f"--{BOUNDARY}\r\nContent-Type: text/plain\r\n\r\nx\r\n".encode()
    with pytest.raises(InvalidRequestError, match="needs a name"):
        read_upload(CONTENT_TYPE, body(part("documents", fields_json()), unnamed))


def test_the_document_count_limit_is_exact() -> None:
    ok = read_upload(CONTENT_TYPE, body(part("documents", fields_json(MAX_DOCUMENTS)), part("f", b"x")))
    assert len(ok.fields) == MAX_DOCUMENTS
    with pytest.raises(InvalidRequestError, match="at most 256 documents"):
        read_upload(CONTENT_TYPE, body(part("documents", fields_json(MAX_DOCUMENTS + 1)), part("f", b"x")))


def test_too_many_file_parts_are_refused() -> None:
    parts = [part("documents", fields_json())] + [part(f"f{i}", b"x") for i in range(MAX_DOCUMENTS + 1)]
    with pytest.raises(InvalidRequestError, match="at most 256 documents"):
        read_upload(CONTENT_TYPE, body(*parts))


@pytest.mark.parametrize(
    "documents",
    [
        b"not json",
        b"5",
        b"null",
        b'["text"]',
        b"[{}]",
        b'[{"doc_id": "", "kind": "text", "part": "f"}]',
        b'[{"doc_id": "d", "kind": "text"}]',
        b'[{"doc_id": "d", "kind": "text", "part": "f", "extra": 1}]',
        b'[{"doc_id": "d", "kind": "text", "part": "f", "metadata": {"a": [1]}}]',
        b'[{"doc_id": "d", "kind": "text", "part": "f", "metadata": {"a": {"b": 1}}}]',
        json.dumps([{"doc_id": "d" * 513, "kind": "text", "part": "f"}]).encode(),
        json.dumps([{"doc_id": "d", "kind": "text", "part": "f", "uri": "u" * 2049}]).encode(),
        b"\xff\xfe",
    ],
)
def test_bad_document_fields_are_refused_without_echo(documents: bytes) -> None:
    with pytest.raises(InvalidRequestError, match="not a valid list of document fields") as error:
        read_upload(CONTENT_TYPE, body(part("documents", documents), part("f", b"x")))
    assert "text" not in error.value.message.replace("document fields", "")


def test_a_documents_part_that_is_an_empty_json_object_is_refused() -> None:
    with pytest.raises(InvalidRequestError):
        read_upload(CONTENT_TYPE, body(part("documents", b"{}"), part("f", b"x")))


def test_metadata_types_and_two_documents_on_one_part_are_kept() -> None:
    documents = json.dumps(
        [
            {"doc_id": "a", "kind": "text", "part": "f", "metadata": {"s": "x", "i": 3, "f": 1.5, "b": True}},
            {"doc_id": "b", "kind": "markdown", "part": "f", "title": "T", "version": "v1", "uri": "u"},
        ]
    ).encode()
    upload = read_upload(CONTENT_TYPE, body(part("documents", documents), part("f", b"shared")))
    raws = raw_documents(upload, "docs")
    assert [r.rag for r in raws] == ["docs", "docs"]
    assert raws[0].metadata == {"s": "x", "i": 3, "f": 1.5, "b": True}
    assert raws[0].source_kind is SourceKind.TEXT
    assert (raws[1].title, raws[1].version, raws[1].uri) == ("T", "v1", "u")
    assert raws[0].data == raws[1].data == b"shared"


def test_an_unknown_kind_is_an_invalid_request() -> None:
    upload = Upload([DocumentFields(doc_id="d", kind="word", part="f")], {"f": b"x"})
    with pytest.raises(InvalidRequestError, match="source kind is unknown"):
        raw_documents(upload, "docs")


def test_a_part_with_a_transfer_encoding_is_refused() -> None:
    encoded = part("f", b"aGVsbG8=", extra_headers="Content-Transfer-Encoding: base64\r\n")
    with pytest.raises(InvalidRequestError, match="no Content-Transfer-Encoding"):
        read_upload(CONTENT_TYPE, body(part("documents", fields_json()), encoded))


@pytest.mark.parametrize(
    "broken",
    [
        b"",
        b"no boundary here",
        f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="documents"\r\n\r\n[]'.encode(),
        f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="documents"\r\n'.encode(),
        f"--{BOUNDARY}garbage\r\n".encode(),
        f"--{BOUNDARY}\r\n".encode() + b"X" * 9000 + b"\r\n\r\n",
    ],
)
def test_broken_multipart_bodies_are_refused_and_never_crash(broken: bytes) -> None:
    with pytest.raises(InvalidRequestError):
        read_upload(CONTENT_TYPE, broken)


def test_a_content_type_without_a_boundary_is_refused() -> None:
    with pytest.raises(InvalidRequestError, match="boundary"):
        read_upload("multipart/form-data", b"--x\r\n")


def test_a_quoted_boundary_is_read() -> None:
    content_type = f'multipart/form-data; boundary="{BOUNDARY}"'
    upload = read_upload(content_type, body(part("documents", fields_json()), part("f", b"x")))
    assert upload.files == {"f": b"x"}


# Jobs.


def make_manager(
    ingest: Callable[[RawDocument], DocumentResult] | None = None, stuck_after: float = 60.0
) -> tuple[JobManager, list[str]]:
    checkpoints: list[str] = []

    def default(document: RawDocument) -> DocumentResult:
        return DocumentResult(document.doc_id, "created", chunks=1, embedded=1)

    return JobManager(ingest or default, checkpoints.append, stuck_after_seconds=stuck_after), checkpoints


def documents(count: int, rag: str = "docs") -> list[RawDocument]:
    return [RawDocument(f"d{i}", rag, SourceKind.TEXT, b"x") for i in range(count)]


def wait_for(condition: Callable[[], bool], seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("the condition did not become true")
        time.sleep(0.005)


def finished(job: Job) -> bool:
    return job.status in {JobStatus.DONE, JobStatus.CANCELLED, JobStatus.FAILED}


def test_a_job_runs_its_documents_in_order_and_writes_a_checkpoint() -> None:
    manager, checkpoints = make_manager()
    try:
        job = manager.submit("docs", documents(3))
        wait_for(lambda: finished(job))
        assert job.status is JobStatus.DONE
        assert [r.doc_id for r in job.results] == ["d0", "d1", "d2"]
        assert checkpoints == ["docs"]
        assert manager.get(job.job_id) is job
        assert job.job_id.startswith("j_")
    finally:
        manager.shutdown()


def test_cancel_stops_a_job_between_documents() -> None:
    started, release = threading.Event(), threading.Event()
    calls: list[str] = []

    def slow(document: RawDocument) -> DocumentResult:
        calls.append(document.doc_id)
        started.set()
        assert release.wait(5)
        return DocumentResult(document.doc_id, "created")

    manager, checkpoints = make_manager(slow)
    try:
        job = manager.submit("docs", documents(4))
        assert started.wait(5)
        assert manager.cancel(job.job_id) is job
        release.set()
        wait_for(lambda: finished(job))
        assert job.status is JobStatus.CANCELLED
        assert calls == ["d0"]
        assert [r.doc_id for r in job.results] == ["d0"]
        assert checkpoints == []
        manager.cancel(job.job_id)  # A second cancel is harmless.
        assert job.status is JobStatus.CANCELLED
    finally:
        release.set()
        manager.shutdown()


def test_a_job_that_is_cancelled_while_queued_never_starts() -> None:
    started, release = threading.Event(), threading.Event()

    def slow(document: RawDocument) -> DocumentResult:
        started.set()
        assert release.wait(5)
        return DocumentResult(document.doc_id, "created")

    manager, _ = make_manager(slow)
    try:
        first = manager.submit("docs", documents(1))
        assert started.wait(5)
        second = manager.submit("docs", documents(2))
        time.sleep(0.05)
        assert second.status is JobStatus.QUEUED  # One job for each RAG at a time.
        manager.cancel(second.job_id)
        release.set()
        wait_for(lambda: finished(first) and finished(second))
        assert first.status is JobStatus.DONE
        assert second.status is JobStatus.CANCELLED
        assert second.results == []
    finally:
        release.set()
        manager.shutdown()


def test_jobs_of_different_rags_run_at_the_same_time() -> None:
    started, release = threading.Event(), threading.Event()

    def ingest(document: RawDocument) -> DocumentResult:
        if document.rag == "slow":
            started.set()
            assert release.wait(5)
        return DocumentResult(document.doc_id, "created")

    manager, _ = make_manager(ingest)
    try:
        slow_job = manager.submit("slow", documents(1, "slow"))
        assert started.wait(5)
        fast_job = manager.submit("fast", documents(1, "fast"))
        wait_for(lambda: finished(fast_job))
        assert slow_job.status is JobStatus.RUNNING
        release.set()
        wait_for(lambda: finished(slow_job))
    finally:
        release.set()
        manager.shutdown()


@pytest.mark.parametrize(
    ("error", "error_type"),
    [
        (InvalidRequestError("bad"), "invalid_request_error"),
        (NotFoundError("gone"), "not_found_error"),
        (ValueError("x"), "internal_error"),
    ],
)
def test_a_failing_document_gets_its_own_result_or_fails_the_job(error: Exception, error_type: str) -> None:
    calls: list[str] = []

    def ingest(document: RawDocument) -> DocumentResult:
        calls.append(document.doc_id)
        if document.doc_id == "d1":
            raise error
        return DocumentResult(document.doc_id, "created")

    manager, checkpoints = make_manager(ingest)
    try:
        job = manager.submit("docs", documents(4))
        wait_for(lambda: finished(job))
        if isinstance(error, XatRagError):
            # ADR 0009: one bad document does not stop the others. It gets its own failed result.
            assert job.status is JobStatus.DONE
            assert calls == ["d0", "d1", "d2", "d3"]
            assert [r.status for r in job.results] == ["created", "failed", "created", "created"]
            assert job.results[1].error_type == error_type
        else:
            # An unexpected error stops the job.
            assert job.status is JobStatus.FAILED
            assert job.error_type == error_type
            assert calls == ["d0", "d1"]
            assert checkpoints == []
        assert job.document_started is None
    finally:
        manager.shutdown()


def test_unknown_jobs_are_not_found() -> None:
    manager, _ = make_manager()
    try:
        with pytest.raises(NotFoundError):
            manager.get("j_missing")
        with pytest.raises(NotFoundError):
            manager.cancel("j_missing")
    finally:
        manager.shutdown()


def test_old_finished_jobs_are_forgotten_and_running_jobs_are_not() -> None:
    started, release = threading.Event(), threading.Event()

    def ingest(document: RawDocument) -> DocumentResult:
        if document.rag == "slow":
            started.set()
            assert release.wait(10)
        return DocumentResult(document.doc_id, "created")

    manager, _ = make_manager(ingest)
    try:
        running = manager.submit("slow", documents(1, "slow"))
        assert started.wait(5)
        jobs: list[Job] = []
        for _ in range(205):
            job = manager.submit("docs", [])
            wait_for(lambda job=job: finished(job))
            jobs.append(job)
        with pytest.raises(NotFoundError):
            manager.get(jobs[0].job_id)
        assert manager.get(jobs[-1].job_id) is jobs[-1]
        assert manager.get(running.job_id) is running
    finally:
        release.set()
        manager.shutdown()


def test_get_and_cancel_do_not_echo_the_job_id() -> None:
    manager, _ = make_manager()
    try:
        with pytest.raises(NotFoundError) as error:
            manager.get("j_secret_value")
        assert "j_secret_value" not in error.value.message
    finally:
        manager.shutdown()


def test_a_failing_checkpoint_is_a_note_and_not_a_failed_job() -> None:
    def checkpoint(rag: str) -> None:
        raise StageError("checkpoint", "disk full")

    manager = JobManager(lambda d: DocumentResult(d.doc_id, "created"), checkpoint, stuck_after_seconds=60)
    try:
        job = manager.submit("docs", documents(2))
        wait_for(lambda: finished(job))
        assert job.status is JobStatus.DONE
        assert job.notes == ["checkpoint_failed:internal_error"]
        assert len(job.results) == 2
    finally:
        manager.shutdown()


def test_a_busy_rag_cannot_be_rebuilt() -> None:
    started, release = threading.Event(), threading.Event()

    def slow(document: RawDocument) -> DocumentResult:
        started.set()
        assert release.wait(10)
        return DocumentResult(document.doc_id, "created")

    manager, _ = make_manager(slow)
    try:
        manager.require_idle("docs")
        job = manager.submit("docs", documents(1))
        assert started.wait(5)
        assert manager.has_active("docs")
        assert not manager.has_active("other")
        with pytest.raises(InvalidRequestError, match="job of this RAG is running"):
            manager.require_idle("docs")
        manager.require_idle("other")
        release.set()
        wait_for(lambda: finished(job))
        manager.require_idle("docs")
    finally:
        release.set()
        manager.shutdown()


class _BigBytes(bytes):
    """Bytes that claim a large size, so that a test needs no memory for 300 MiB."""

    def __new__(cls, size: int) -> "_BigBytes":
        instance = super().__new__(cls, b"x")
        instance.claimed = size
        return instance

    claimed: int = 0

    def __len__(self) -> int:
        return self.claimed


def test_too_much_waiting_data_is_refused_until_a_job_ends() -> None:
    started, release = threading.Event(), threading.Event()

    def slow(document: RawDocument) -> DocumentResult:
        started.set()
        assert release.wait(10)
        return DocumentResult(document.doc_id, "created")

    manager, _ = make_manager(slow)
    mib = 1024 * 1024
    try:
        first = manager.submit("docs", [RawDocument("big", "docs", SourceKind.TEXT, _BigBytes(200 * mib))])
        assert started.wait(5)
        with pytest.raises(OverloadedError) as error:
            manager.submit("docs", [RawDocument("big2", "docs", SourceKind.TEXT, _BigBytes(100 * mib))])
        assert error.value.retry_after_seconds == 5
        manager.submit("docs", [RawDocument("small", "docs", SourceKind.TEXT, b"x")])  # A small job still fits.
        release.set()
        wait_for(lambda: finished(first))
        wait_for(lambda: not manager.has_active("docs"))
        second = manager.submit("docs", [RawDocument("big3", "docs", SourceKind.TEXT, _BigBytes(100 * mib))])
        wait_for(lambda: finished(second))
    finally:
        release.set()
        manager.shutdown()


def test_a_parse_that_runs_too_long_makes_the_manager_degraded_for_good(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("xat_rag_service.jobs._WATCH_SECONDS", 0.02)
    started, release = threading.Event(), threading.Event()

    def stuck(document: RawDocument) -> DocumentResult:
        started.set()
        assert release.wait(10)
        return DocumentResult(document.doc_id, "created")

    manager, _ = make_manager(stuck, stuck_after=0.05)
    try:
        manager.check_degraded()  # Not degraded yet.
        job = manager.submit("docs", documents(1))
        assert started.wait(5)
        wait_for(lambda: manager.degraded_reason is not None)
        with pytest.raises(ServiceUnavailableError) as error:
            manager.check_degraded()
        assert error.value.retry_after_seconds is None
        assert manager.degraded_reason == "parse_timeout"
        release.set()
        wait_for(lambda: finished(job))
        with pytest.raises(ServiceUnavailableError):
            manager.submit("docs", documents(1))  # A restart is needed: the state does not clear.
    finally:
        release.set()
        manager.shutdown()


def test_a_fast_parse_does_not_make_the_manager_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("xat_rag_service.jobs._WATCH_SECONDS", 0.02)
    manager, _ = make_manager(stuck_after=5.0)
    try:
        jobs = [manager.submit("docs", documents(2)) for _ in range(5)]
        wait_for(lambda: all(finished(job) for job in jobs))
        time.sleep(0.1)
        assert manager.degraded_reason is None
    finally:
        manager.shutdown()


def test_shutdown_cancels_the_jobs_and_waits_for_the_running_document() -> None:
    started, release = threading.Event(), threading.Event()

    def slow(document: RawDocument) -> DocumentResult:
        started.set()
        assert release.wait(10)
        return DocumentResult(document.doc_id, "created")

    manager, _ = make_manager(slow)
    job = manager.submit("docs", documents(3))
    assert started.wait(5)
    threading.Timer(0.2, release.set).start()
    began = time.monotonic()
    manager.shutdown()
    assert time.monotonic() - began >= 0.15  # It waited for the running document.
    wait_for(lambda: finished(job))
    assert job.status is JobStatus.CANCELLED
    assert len(job.results) == 1


def test_shutdown_gives_up_after_its_timeout() -> None:
    started, release = threading.Event(), threading.Event()

    def stuck(document: RawDocument) -> DocumentResult:
        started.set()
        release.wait(10)
        return DocumentResult(document.doc_id, "created")

    manager, _ = make_manager(stuck)
    try:
        manager.submit("docs", documents(1))
        assert started.wait(5)
        began = time.monotonic()
        manager.shutdown(timeout_seconds=0.2)
        assert time.monotonic() - began < 3
    finally:
        release.set()


# The limiter and the errors.


# On Windows, the event loop makes its internal socket pair over loopback.
@pytest.mark.allow_hosts(["127.0.0.1"])
def test_the_limiter_refuses_work_over_its_limit_and_frees_the_slot() -> None:
    async def scenario() -> None:
        limiter = Limiter(queue_limit=2, workers=2)
        started, release = threading.Event(), threading.Event()

        def block() -> str:
            started.set()
            release.wait(5)
            return "done"

        try:
            first = asyncio.create_task(limiter.run(block))
            second = asyncio.create_task(limiter.run(block))
            while not started.is_set():
                await asyncio.sleep(0.005)
            with pytest.raises(OverloadedError) as error:
                await limiter.run(lambda: 1)
            assert error.value.retry_after_seconds == 1
            release.set()
            assert await first == await second == "done"
            assert await limiter.run(lambda: 5) == 5
        finally:
            release.set()
            limiter.shutdown()

    asyncio.run(scenario())


# On Windows, the event loop makes its internal socket pair over loopback.
@pytest.mark.allow_hosts(["127.0.0.1"])
def test_the_limiter_frees_the_slot_when_the_work_fails() -> None:
    async def scenario() -> None:
        limiter = Limiter(queue_limit=1)

        def boom() -> None:
            raise InternalError("boom")

        try:
            for _ in range(3):
                with pytest.raises(InternalError):
                    await limiter.run(boom)
            assert await limiter.run(lambda: "ok") == "ok"
        finally:
            limiter.shutdown()

    asyncio.run(scenario())


def test_retry_after_is_set_only_when_a_retry_helps() -> None:
    assert error_response(OverloadedError("busy")).headers["retry-after"] == "1"
    assert error_response(ServiceUnavailableError("loading", retry_after_seconds=5)).headers["retry-after"] == "5"
    degraded = error_response(ServiceUnavailableError("restart", retry_after_seconds=None))
    assert "retry-after" not in degraded.headers
    assert degraded.status_code == 503
    other = error_response(NotFoundError("gone"))
    assert other.status_code == 404
    assert "retry-after" not in other.headers
    assert json.loads(bytes(other.body)) == {"error": {"message": "gone", "type": "not_found_error"}}


# Helpers of the run module and the serializers.


def test_the_ready_file_is_written_whole(tmp_path: Path) -> None:
    path = tmp_path / "ready.json"
    write_ready_file(path, "http://127.0.0.1:5000")
    first = json.loads(path.read_text())
    assert set(first) == {"url", "pid", "version"}
    assert first["url"] == "http://127.0.0.1:5000"
    write_ready_file(path, "http://127.0.0.1:6000")
    assert json.loads(path.read_text())["url"] == "http://127.0.0.1:6000"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ready.json"]


def test_only_one_service_can_hold_a_data_folder(tmp_path: Path) -> None:
    folder = tmp_path / "new" / "data"
    first = lock_data_folder(folder)
    try:
        assert folder.is_dir()
        with pytest.raises(ConfigError, match="Another service uses the data folder"):
            lock_data_folder(folder)
    finally:
        first.close()
    lock_data_folder(folder).close()


def test_a_data_folder_that_cannot_be_made_is_a_config_error(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(ConfigError, match="cannot write to the data folder"):
        lock_data_folder(blocker / "data")


def test_a_ready_file_that_cannot_be_deleted_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot delete the ready file"):
        remove_ready_file(tmp_path)


def test_a_missing_ready_file_needs_no_delete(tmp_path: Path) -> None:
    remove_ready_file(tmp_path / "ready.json")


def test_a_failed_ready_file_write_leaves_no_temporary_file(tmp_path: Path) -> None:
    target = tmp_path / "ready.json"
    target.mkdir()
    (target / "child").write_text("x")  # `os.replace` cannot put a file over a folder.
    with pytest.raises(OSError):  # noqa: PT011 - the operating system chooses the subclass.
        write_ready_file(target, "http://127.0.0.1:5000")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ready.json"]


def test_logs_of_libraries_that_can_hold_content_are_off() -> None:
    from xat_rag_service.run import configure_logs  # noqa: PLC0415

    configure_logs()
    for name in ("pypdf", "docx", "bs4", "uvicorn.access", "httpx", "langsmith", "onnxruntime"):
        assert not logging.getLogger(name).isEnabledFor(logging.CRITICAL)


def test_span_and_result_json() -> None:
    assert span_json(LineSpan(3, 9)) == {"type": "line", "line_start": 3, "line_end": 9}
    assert span_json(BlockSpan(1, 4)) == {"type": "block", "block_start": 1, "block_end": 4}
    assert span_json(PageSpan(2, 3, "ii", "iii")) == {
        "type": "page",
        "page_start": 2,
        "page_end": 3,
        "label_start": "ii",
        "label_end": "iii",
    }
    result = DocumentResult(
        "d",
        "failed",
        error_type="parse_error",
        reason="invalid",
        skipped_pages=(SkippedPage(2, "ii", "no_text"),),
        notes=("n",),
    )
    data = document_result_json(result)
    assert data["skipped_pages"] == [{"page": 2, "label": "ii", "reason": "no_text"}]
    assert data["notes"] == ["n"]
    assert data["error_type"] == "parse_error"
    json.dumps(data)


def test_xat_rag_error_base_class_is_exported_for_the_handlers() -> None:
    assert issubclass(ConfigError, XatRagError)


def _chunk(**changes: object) -> RetrievedChunk:
    source = ChunkSource("docs", "d1", "pdf", "a/b.pdf#page=2", "Title", "v1", PageSpan(2, 3, "ii", "iii"))
    values: dict[str, object] = {
        "chunk_id": "d1#a",
        "parent_id": None,
        "kind": "page",
        "score": 0.5,
        "size_tokens": 7,
        "header": "H",
        "text": "T",
        "source": source,
        "metadata": {"team": "core", "n": 3, "ok": True},
    }
    values.update(changes)
    return RetrievedChunk(**values)  # type: ignore[arg-type]


def test_chunk_json_has_the_chunk_form() -> None:
    assert chunk_json(_chunk()) == {
        "chunk_id": "d1#a",
        "parent_id": None,
        "kind": "page",
        "score": 0.5,
        "size_tokens": 7,
        "header": "H",
        "text": "T",
        "source": {
            "rag": "docs",
            "doc_id": "d1",
            "source_kind": "pdf",
            "uri": "a/b.pdf#page=2",
            "title": "Title",
            "version": "v1",
            "span": {
                "type": "page",
                "page_start": 2,
                "page_end": 3,
                "label_start": "ii",
                "label_end": "iii",
            },
        },
        "metadata": {"team": "core", "n": 3, "ok": True},
    }


def test_result_json_has_chunks_omitted_and_debug() -> None:
    plain = result_json(RetrievalResult((_chunk(),), (OmittedItem("x#1", "limit"), OmittedItem("x#2", "duplicate"))))
    assert set(plain) == {"chunks", "omitted", "debug"}
    assert plain["omitted"] == [{"chunk_id": "x#1", "reason": "limit"}, {"chunk_id": "x#2", "reason": "duplicate"}]
    assert plain["debug"] is None
    debug = result_json(RetrievalResult((), (), (DebugCandidate("c", "docs", 1, None, 0.1, 0.2, False),)))
    assert debug["debug"] == [
        {
            "chunk_id": "c",
            "rag": "docs",
            "lexical_rank": 1,
            "dense_rank": None,
            "rrf_score": 0.1,
            "rerank_score": 0.2,
            "rerank_truncated": False,
        }
    ]
    assert result_json(RetrievalResult(()))["debug"] is None
    json.dumps(plain)


def test_query_json_has_the_three_texts_and_no_lookups() -> None:
    data = query_json(RetrievalQuery(("a", "b"), "dense", "rerank"))
    assert data == {"lexical_terms": ["a", "b"], "dense_text": "dense", "rerank_text": "rerank"}


def test_result_json_keeps_the_low_score_reason() -> None:
    assert result_json(RetrievalResult((), (OmittedItem("x#1", "low_score"),)))["omitted"] == [
        {"chunk_id": "x#1", "reason": "low_score"}
    ]
