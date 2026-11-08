"""Milestone 5 tests of the service routes with the real models: uploads, jobs, limits, errors and health."""

import io
import itertools
import json
import threading
import time
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import docx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter
from service_setup import KEY, LOCK, MODELS, multipart, write_setup
from xat_rag import Settings, XatRag
from xat_rag.types import DocumentResult
from xat_rag_core.model import RawDocument
from xat_rag_service.app import MAX_UPLOAD_BYTES, create_app

pytestmark = pytest.mark.skipif(not (MODELS / "bge-small-en-v1.5" / "model.onnx").exists(), reason="models not fetched")

AUTH = {"Authorization": f"Bearer {KEY}"}
DOC = (
    b"# Handbook\n\n## Orders\n\nThe orders service runs on port 8080 and stores data in PostgreSQL.\n\n"
    b"## Food\n\nPasta on Friday.\n"
)

MIXED_RAG = """
[rag]
name = "mixed"
embedder = "bge-small-en-v1.5"
reranker = "ms-marco-minilm-l6-v2"

[sources.pdf]
kind = "pdf"

[sources.docx]
kind = "docx"

[sources.html]
kind = "html"

[sources.md]
kind = "markdown"
"""

LOGS_CONTEXT = """
[context]
name = "logs"
generator = "ministral-3-3b"
query_builder = "log-v1"
pinned = ["prompt:helper@1"]

[[context.sources]]
rag = "docs"
"""

TINY_CONTEXT = """
[context]
name = "tiny"
generator = "ministral-3-3b"
window = 256
reserved_output = 200
input_max_tokens = 100

[[context.sources]]
rag = "docs"
"""

WORDS = [
    "order",
    "queue",
    "cache",
    "index",
    "table",
    "record",
    "batch",
    "retry",
    "timeout",
    "socket",
    "thread",
    "buffer",
    "stream",
    "request",
    "response",
]


def make_pdf(pages: list[str], password: str | None = None) -> bytes:
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(len(pages)))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    font_id = 3 + len(pages) * 2
    for index, text in enumerate(pages):
        content_id = 4 + index * 2
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        stream = f"BT /F1 12 Tf 72 712 Td ({text}) Tj ET".encode() if text else b""
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    data = out.getvalue()
    if password is None:
        return data
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(data)))
    writer.encrypt(password, algorithm="RC4-128")
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


def make_docx(*, macro: bool = False) -> bytes:
    document = docx.Document()
    document.add_heading("Billing", level=1)
    document.add_paragraph("Use TLS for every billing connection on port 9443.")
    paragraph = document.add_paragraph("Visible ")
    paragraph.add_run("HIDDEN-DOCX-SECRET").font.hidden = True
    buffer = io.BytesIO()
    document.save(buffer)
    data = buffer.getvalue()
    if not macro:
        return data
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for item in source.infolist():
            target.writestr(item, source.read(item.filename))
        target.writestr("word/vbaProject.bin", b"\x00" * 10)
    return out.getvalue()


HTML = b"<html><body><h1>Gateway</h1><p>Set HTTPS_PROXY before you restart the gateway.</p></body></html>"


def prepare(folder: Path) -> Settings:
    env = write_setup(folder)
    recipes = folder / "recipes"
    (recipes / "rags" / "mixed.toml").write_text(MIXED_RAG)
    (recipes / "contexts" / "logs.toml").write_text(LOGS_CONTEXT)
    (recipes / "contexts" / "tiny.toml").write_text(TINY_CONTEXT)
    return Settings(
        data_folder=Path(env["XatRag__DataFolder"]),
        recipes_folder=recipes,
        models_folder=MODELS,
        lock_file=LOCK,
        prompts_folder=Path(env["XatRag__PromptsFolder"]),
        threads=1,
    )


def upload(
    client: TestClient,
    rag: str,
    documents: list[dict[str, Any]],
    files: dict[str, bytes],
    headers: dict[str, str] | None = None,
) -> Any:
    body, content_type = multipart(json.dumps(documents), files)
    return client.put(
        f"/v1/rags/{rag}/documents", content=body, headers={**(headers or AUTH), "Content-Type": content_type}
    )


def wait_job(client: TestClient, job_id: str, seconds: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        job: dict[str, Any] = client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
        if job["status"] in {"done", "failed", "cancelled"}:
            return job
        time.sleep(0.02)
    raise AssertionError("the job did not end")


@pytest.fixture(scope="module")
def rag(tmp_path_factory: pytest.TempPathFactory) -> Iterator[XatRag]:
    library = XatRag.open(prepare(tmp_path_factory.mktemp("routes")))
    yield library
    library.close()


@pytest.fixture
def make_client(rag: XatRag) -> Iterator[Callable[..., TestClient]]:
    apps: list[FastAPI] = []

    def build(queue_limit: int = 8) -> TestClient:
        app = create_app(rag, KEY, queue_limit=queue_limit, version="m5")
        apps.append(app)
        return TestClient(app, base_url="http://127.0.0.1")

    yield build
    for app in apps:
        app.state.jobs.shutdown()
        app.state.limiter.shutdown()


@pytest.fixture
def client(make_client: Callable[..., TestClient], rag: XatRag) -> TestClient:
    result = make_client()
    if not rag.index("docs").store.has_document("docs:handbook.md"):
        fields = [{"doc_id": "docs:handbook.md", "kind": "markdown", "part": "f"}]
        wait_job(result, upload(result, "docs", fields, {"f": DOC}).json()["job_id"])
    return result


# Uploads.


def test_pdf_docx_html_and_markdown_in_one_upload(client: TestClient) -> None:
    fields = [
        {"doc_id": "mixed:manual.pdf", "kind": "pdf", "part": "p", "uri": "https://wiki/manual.pdf"},
        {"doc_id": "mixed:spec.docx", "kind": "docx", "part": "d"},
        {"doc_id": "mixed:guide.html", "kind": "html", "part": "h"},
        {"doc_id": "mixed:note.md", "kind": "markdown", "part": "m"},
    ]
    pdf = make_pdf(["Overview: the shipping depot opens at six in the morning every working day.", ""])
    files = {"p": pdf, "d": make_docx(), "h": HTML, "m": b"# Note\n\nThe depot keeps spare parcels.\n"}
    response = upload(client, "mixed", fields, files)
    assert response.status_code == 202, response.text
    job = wait_job(client, response.json()["job_id"])
    assert job["status"] == "done"
    assert (job["total"], job["done"], job["rag"]) == (4, 4, "mixed")
    assert [r["status"] for r in job["results"]] == ["created"] * 4
    assert job["results"][0]["pages_total"] == 2
    assert job["results"][0]["skipped_pages"] == [{"page": 2, "label": "2", "reason": "no_text"}]
    hits = client.post("/v1/rags/mixed/search", json={"question": "shipping depot opens"}, headers=AUTH).json()["hits"]
    assert hits[0]["chunk_id"].startswith("mixed:manual.pdf#")
    assert hits[0]["span"]["type"] == "page"
    assert hits[0]["uri"] == "https://wiki/manual.pdf"
    spans = {hit["chunk_id"].split("#")[0]: hit["span"]["type"] for hit in hits}
    assert set(spans.values()) <= {"page", "block", "line"}
    billing = client.post("/v1/rags/mixed/search", json={"question": "billing TLS port 9443"}, headers=AUTH).json()
    assert billing["hits"][0]["chunk_id"].startswith("mixed:spec.docx#")
    for doc_id in ("mixed:manual.pdf", "mixed:spec.docx", "mixed:guide.html", "mixed:note.md"):
        assert client.delete(f"/v1/rags/mixed/documents/{doc_id}", headers=AUTH).json() == {"deleted": True}


def test_failed_documents_do_not_fail_the_job(client: TestClient) -> None:
    fields = [
        {"doc_id": "mixed:enc.pdf", "kind": "pdf", "part": "a"},
        {"doc_id": "mixed:macro.docx", "kind": "docx", "part": "b"},
        {"doc_id": "mixed:blank.pdf", "kind": "pdf", "part": "c"},
        {"doc_id": "mixed:good.md", "kind": "markdown", "part": "d"},
    ]
    files = {
        "a": make_pdf(["Secret page about an encrypted file for the test run."], password="pw"),
        "b": make_docx(macro=True),
        "c": make_pdf(["", ""]),
        "d": b"# Good\n\nA good note about the depot.\n",
    }
    job = wait_job(client, upload(client, "mixed", fields, files).json()["job_id"])
    assert job["status"] == "done"
    assert [(r["status"], r["reason"]) for r in job["results"]] == [
        ("failed", "encrypted"),
        ("failed", "unsupported_media_type"),
        ("failed", "no_text_layer"),
        ("created", None),
    ]
    assert all(r["error_type"] == "parse_error" for r in job["results"][:3])
    assert "Secret page" not in json.dumps(job)
    client.delete("/v1/rags/mixed/documents/mixed:good.md", headers=AUTH)


def good_fields(count: int = 1) -> list[dict[str, Any]]:
    return [{"doc_id": f"docs:u{i}.md", "kind": "markdown", "part": "f"} for i in range(count)]


def test_upload_without_a_documents_part_is_a_400(client: TestClient) -> None:
    body, content_type = multipart("[]", {"f": b"x"})
    body = body.replace(b'name="documents"', b'name="other"')
    response = client.put("/v1/rags/docs/documents", content=body, headers={**AUTH, "Content-Type": content_type})
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert "'documents' part" in response.json()["error"]["message"]


def test_upload_with_an_unknown_part_is_a_400(client: TestClient) -> None:
    fields = [{"doc_id": "docs:x.md", "kind": "markdown", "part": "missing"}]
    response = upload(client, "docs", fields, {"f": b"x"})
    assert response.status_code == 400
    assert "file part that is not in the upload" in response.json()["error"]["message"]


def test_upload_with_too_many_documents_is_a_400(client: TestClient) -> None:
    response = upload(client, "docs", good_fields(257), {"f": b"# T\n\nx"})
    assert response.status_code == 400
    assert "at most 256 documents" in response.json()["error"]["message"]


@pytest.mark.parametrize("content_type", ["application/json", "text/plain", "multipart/mixed; boundary=x", ""])
def test_upload_with_a_wrong_content_type_is_a_400(client: TestClient, content_type: str) -> None:
    headers = {**AUTH, "Content-Type": content_type} if content_type else AUTH
    response = client.put("/v1/rags/docs/documents", content=b'{"a": 1}', headers=headers)
    assert response.status_code == 400
    assert "multipart/form-data" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    "documents",
    ["not json", '[{"doc_id": "x", "kind": "markdown"}]', '[{"doc_id": "x", "kind": "markdown", "part": "f", "z": 1}]'],
)
def test_upload_with_bad_document_fields_is_a_400(client: TestClient, documents: str) -> None:
    body, content_type = multipart(documents, {"f": b"x"})
    response = client.put("/v1/rags/docs/documents", content=body, headers={**AUTH, "Content-Type": content_type})
    assert response.status_code == 400
    assert "not a valid list of document fields" in response.json()["error"]["message"]


def test_upload_with_an_unknown_kind_or_rag(client: TestClient) -> None:
    kind = upload(client, "docs", [{"doc_id": "x", "kind": "word", "part": "f"}], {"f": b"x"})
    assert (kind.status_code, kind.json()["error"]["type"]) == (400, "invalid_request_error")
    rag = upload(client, "ghost", good_fields(), {"f": b"x"})
    assert (rag.status_code, rag.json()["error"]["type"]) == (404, "not_found_error")


def test_upload_with_an_empty_body_is_a_400(client: TestClient) -> None:
    response = client.put(
        "/v1/rags/docs/documents", headers={**AUTH, "Content-Type": "multipart/form-data; boundary=x"}
    )
    assert response.status_code == 400


def test_a_document_kind_that_the_rag_does_not_take_does_not_drop_the_rest_of_the_batch(client: TestClient) -> None:
    fields = [
        {"doc_id": "docs:ok1.md", "kind": "markdown", "part": "a"},
        {"doc_id": "docs:bad.pdf", "kind": "pdf", "part": "b"},
        {"doc_id": "docs:ok2.md", "kind": "markdown", "part": "c"},
    ]
    files = {
        "a": b"# One\n\nFirst note.\n",
        "b": make_pdf(["A page of text for the kind check in this test."]),
        "c": b"# Two\n\nSecond note.\n",
    }
    response = upload(client, "docs", fields, files)
    try:
        if response.status_code == 400:
            return
        job = wait_job(client, response.json()["job_id"])
        assert job["status"] == "done"
        assert len(job["results"]) == 3
    finally:
        for name in ("docs:ok1.md", "docs:ok2.md"):
            client.delete(f"/v1/rags/docs/documents/{name}", headers=AUTH)


# Size limits.


def test_a_declared_upload_over_64_mib_is_a_413(client: TestClient) -> None:
    headers = {**AUTH, "Content-Type": "multipart/form-data; boundary=x", "Content-Length": str(MAX_UPLOAD_BYTES + 1)}
    response = client.put("/v1/rags/docs/documents", content=b"tiny", headers=headers)
    assert response.status_code == 413
    assert response.json() == {"error": {"message": "The request body is too large.", "type": "invalid_request_error"}}


def test_an_upload_over_64_mib_without_a_declared_length_is_a_413(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(65):
            yield b"a" * (1024 * 1024)

    headers = {**AUTH, "Content-Type": "multipart/form-data; boundary=x"}
    response = client.put("/v1/rags/docs/documents", content=chunks(), headers=headers)
    assert response.status_code == 413


def test_the_access_key_is_checked_before_the_size(client: TestClient) -> None:
    headers = {"Content-Type": "multipart/form-data; boundary=x", "Content-Length": str(MAX_UPLOAD_BYTES + 1)}
    assert client.put("/v1/rags/docs/documents", content=b"tiny", headers=headers).status_code == 401


def test_a_json_body_over_1_mib_is_a_413_on_every_json_route(client: TestClient) -> None:
    big = b" " * (1024 * 1024 + 1)
    for path in (
        "/v1/context",
        "/v1/query/build",
        "/v1/tokens/count",
        "/v1/citations/validate",
        "/v1/rags/docs/search",
    ):
        response = client.post(path, content=big, headers={**AUTH, "Content-Type": "application/json"})
        assert response.status_code == 413, path


# Jobs.


def test_cancel_route_stops_a_running_job_between_documents(
    make_client: Callable[..., TestClient], rag: XatRag, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, release = threading.Event(), threading.Event()
    seen: list[str] = []

    def slow(document: RawDocument) -> DocumentResult:
        seen.append(document.doc_id)
        started.set()
        assert release.wait(10)
        return DocumentResult(document.doc_id, "created")

    monkeypatch.setattr(rag, "ingest_one", slow)
    client = make_client()
    try:
        job_id = upload(client, "docs", good_fields(3), {"f": b"# T\n\nx"}).json()["job_id"]
        assert started.wait(10)
        cancelled = client.delete(f"/v1/jobs/{job_id}", headers=AUTH)
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] in {"running", "cancelled"}
        release.set()
        job = wait_job(client, job_id)
        assert job["status"] == "cancelled"
        assert (job["total"], job["done"]) == (3, 1)
        assert seen == ["docs:u0.md"]
        assert set(job) == {"job_id", "rag", "status", "total", "done", "error_type", "notes", "results"}
        again = client.delete(f"/v1/jobs/{job_id}", headers=AUTH)
        assert again.status_code == 200
        assert again.json()["status"] == "cancelled"
    finally:
        release.set()


def test_a_rag_with_a_running_job_cannot_be_rebuilt_and_other_rags_can(
    make_client: Callable[..., TestClient], rag: XatRag, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, release = threading.Event(), threading.Event()

    def slow(document: RawDocument) -> DocumentResult:
        started.set()
        assert release.wait(10)
        return DocumentResult(document.doc_id, "created")

    monkeypatch.setattr(rag, "ingest_one", slow)
    client = make_client()
    try:
        job_id = upload(client, "manuals", [{"doc_id": "m:a", "kind": "pdf", "part": "f"}], {"f": b"x"}).json()[
            "job_id"
        ]
        assert started.wait(10)
        busy = client.post("/v1/rags/manuals/rebuild", headers=AUTH)
        assert busy.status_code == 400
        assert busy.json()["error"]["type"] == "invalid_request_error"
        assert "job of this RAG is running" in busy.json()["error"]["message"]
        assert client.post("/v1/rags/mixed/rebuild", headers=AUTH).status_code == 200
        release.set()
        assert wait_job(client, job_id)["status"] == "done"
        assert client.post("/v1/rags/manuals/rebuild", headers=AUTH).status_code == 200
    finally:
        release.set()


def test_unknown_jobs_are_404(client: TestClient) -> None:
    for method in (client.get, client.delete):
        response = method("/v1/jobs/j_nope", headers=AUTH)
        assert (response.status_code, response.json()["error"]["type"]) == (404, "not_found_error")


# Overload.


def test_overloaded_with_a_queue_limit_of_one(
    make_client: Callable[..., TestClient], rag: XatRag, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, release = threading.Event(), threading.Event()

    def slow_count(text: str, generator: str) -> int:
        started.set()
        assert release.wait(10)
        return 1

    monkeypatch.setattr(rag, "count_tokens", slow_count)
    client = make_client(queue_limit=1)
    first: list[Any] = []
    worker = threading.Thread(
        target=lambda: first.append(client.post("/v1/tokens/count", json={"text": "a"}, headers=AUTH))
    )
    worker.start()
    try:
        assert started.wait(10)
        second = client.post("/v1/tokens/count", json={"text": "b"}, headers=AUTH)
        assert second.status_code == 503
        assert second.json()["error"]["type"] == "overloaded_error"
        assert second.headers["Retry-After"] == "1"
        assert client.get("/health/live").status_code == 200  # Routes with no queue still answer.
        assert client.get("/v1/jobs/j_none", headers=AUTH).status_code == 404
    finally:
        release.set()
        worker.join(10)
    assert first[0].status_code == 200
    assert client.post("/v1/tokens/count", json={"text": "c"}, headers=AUTH).status_code == 200


def test_concurrent_requests_over_the_queue_limit_are_refused_and_the_rest_complete(
    make_client: Callable[..., TestClient], rag: XatRag, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()

    def slow_count(text: str, generator: str) -> int:
        assert release.wait(10)
        return 1

    monkeypatch.setattr(rag, "count_tokens", slow_count)
    client = make_client(queue_limit=2)
    responses: list[Any] = []
    lock = threading.Lock()

    def call() -> None:
        response = client.post("/v1/tokens/count", json={"text": "a"}, headers=AUTH)
        with lock:
            responses.append(response)

    threads = [threading.Thread(target=call) for _ in range(5)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with lock:
            if len(responses) >= 3:
                break
        time.sleep(0.01)
    with lock:
        assert sorted(r.status_code for r in responses) == [503, 503, 503]
    release.set()
    for thread in threads:
        thread.join(10)
    assert sorted(r.status_code for r in responses) == [200, 200, 503, 503, 503]


# Index mismatch.


@pytest.fixture
def mismatched(tmp_path: Path) -> Iterator[tuple[TestClient, XatRag]]:
    settings = prepare(tmp_path)
    XatRag.open(settings).close()
    recipe = (tmp_path / "recipes" / "rags" / "docs.toml").read_text()
    (tmp_path / "recipes" / "rags" / "docs.toml").write_text(recipe + "max_chunk_tokens = 111\n")
    library = XatRag.open(settings)
    app = create_app(library, KEY, queue_limit=4, version="m5")
    yield TestClient(app, base_url="http://127.0.0.1"), library
    app.state.jobs.shutdown()
    app.state.limiter.shutdown()
    library.close()


def test_a_rag_that_needs_a_rebuild_gives_409_on_its_routes(mismatched: tuple[TestClient, XatRag]) -> None:
    client, _ = mismatched
    calls: list[tuple[str, str, dict[str, Any]]] = [
        ("get", "/v1/rags/docs", {}),
        ("post", "/v1/rags/docs/search", {"json": {"question": "orders"}}),
        ("post", "/v1/context", {"json": {"context": "ask", "question": "orders"}}),
        ("delete", "/v1/rags/docs/documents/docs:x.md", {}),
    ]
    for method, path, extra in calls:
        response = getattr(client, method)(path, headers=AUTH, **extra)
        assert response.status_code == 409, path
        assert response.json()["error"]["type"] == "index_mismatch_error", path
    body, content_type = multipart(json.dumps(good_fields()), {"f": b"# T\n\nx"})
    put = client.put("/v1/rags/docs/documents", content=body, headers={**AUTH, "Content-Type": content_type})
    assert put.status_code == 409
    ready = client.get("/health/ready", headers=AUTH)
    assert (ready.status_code, ready.json()["needs_rebuild"]) == (200, ["docs"])
    listing = client.get("/v1/rags", headers=AUTH).json()
    by_name = {item["name"]: item for item in listing["rags"]}
    assert by_name["docs"] == {"name": "docs", "needs_rebuild": True}
    assert by_name["manuals"]["needs_rebuild"] is False
    assert client.get("/v1/rags/manuals", headers=AUTH).status_code == 200
    assert client.get("/v1/rags/mixed", headers=AUTH).status_code == 200
    assert "ask" in listing["contexts"]


def test_rebuild_route_clears_the_mismatch(mismatched: tuple[TestClient, XatRag]) -> None:
    client, library = mismatched
    assert client.post("/v1/rags/ghost/rebuild", headers=AUTH).status_code == 404
    response = client.post("/v1/rags/docs/rebuild", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["rebuilt"] is True
    assert library.needs_rebuild() == {}
    assert client.get("/v1/rags/docs", headers=AUTH).json()["documents"] == 0
    assert client.get("/health/ready", headers=AUTH).json()["needs_rebuild"] == []
    assert client.post("/v1/context", json={"context": "ask", "question": "orders"}, headers=AUTH).status_code == 200


# Health.


def test_ready_is_degraded_when_the_job_manager_says_so(make_client: Callable[..., TestClient]) -> None:
    client = make_client()
    ready = client.get("/health/ready", headers=AUTH)
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    client.app.state.jobs.degraded_reason = "parse_timeout"  # type: ignore[attr-defined]
    degraded = client.get("/health/ready", headers=AUTH)
    assert degraded.status_code == 503
    assert degraded.json() == {"status": "degraded", "reason": "parse_timeout"}
    assert client.get("/health/live").status_code == 200


def test_a_degraded_service_refuses_new_uploads_without_a_retry_hint_and_still_reads(
    make_client: Callable[..., TestClient],
) -> None:
    client = make_client()
    client.app.state.jobs.degraded_reason = "parse_timeout"  # type: ignore[attr-defined]
    response = upload(client, "docs", good_fields(), {"f": b"# T\n\nx"})
    assert response.status_code == 503
    assert response.json()["error"]["type"] == "service_unavailable_error"
    assert "Retry-After" not in response.headers
    assert client.get("/v1/rags", headers=AUTH).status_code == 200
    assert client.post("/v1/tokens/count", json={"text": "a"}, headers=AUTH).status_code == 200


# Context route.


def test_context_budget_in_the_json_adds_up_and_overrides_work(client: TestClient) -> None:
    response = client.post(
        "/v1/context", json={"context": "ask", "question": "Which port?", "debug": True}, headers=AUTH
    )
    data = response.json()
    budget = data["budget"]
    parts = ("reserved_output", "overhead", "pinned", "input", "chunks", "margin", "unused")
    assert sum(budget[name] for name in parts) == budget["window"] == 8192
    assert data["debug"]
    smaller = client.post(
        "/v1/context", json={"context": "ask", "question": "Which port?", "reserved_output": 100}, headers=AUTH
    ).json()
    assert smaller["max_tokens"] == 100
    assert smaller["debug"] is None


@pytest.mark.parametrize("field", ["reserved_output", "input_max_tokens"])
def test_context_overrides_above_the_recipe_are_a_400(client: TestClient, field: str) -> None:
    for value in (10**6, 0, -1):
        response = client.post("/v1/context", json={"context": "ask", "question": "q", field: value}, headers=AUTH)
        assert response.status_code == 400, (field, value)
        assert response.json()["error"]["type"] == "invalid_request_error"
        assert f"'{field}'" in response.json()["error"]["message"]


def test_a_tiny_window_is_a_422(client: TestClient) -> None:
    response = client.post("/v1/context", json={"context": "tiny", "question": "Which port?"}, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["error"]["type"] == "context_too_large_error"
    assert "Which port" not in response.text


def test_many_log_lines_report_dropped_templates(client: TestClient) -> None:
    triples = itertools.islice(itertools.product(WORDS, WORDS, WORDS), 400)
    lines = [f"INFO worker {a} {b} finished {c} ok" for a, b, c in triples]
    lines.append("ERROR [orders] OrderNotFoundException: order X not found")
    response = client.post(
        "/v1/context", json={"context": "logs", "log_lines": lines, "input_max_tokens": 200}, headers=AUTH
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["input"]["dropped_templates"] > 0
    assert data["input"]["tokens"] <= 200
    assert "OrderNotFoundException" in data["input"]["text"]
    assert data["rendered"]["messages"][0]["content"].startswith("You answer")


def test_context_body_limits_and_shapes(client: TestClient) -> None:
    too_many = client.post("/v1/context", json={"context": "logs", "log_lines": ["x"] * 2001}, headers=AUTH)
    assert too_many.status_code == 400
    assert "log_lines" in too_many.json()["error"]["message"]
    for body in (
        {"context": "ask"},
        {"context": "ask", "question": ""},
        {"context": "ask", "question": "   "},
        {"context": "", "question": "q"},
        {"context": "ask", "question": "q", "unknown": 1},
        {"context": "ask", "question": "q", "log_lines": ["a"]},
        {"context": "ask", "question": "q", "debug": "maybe"},
        {"context": "ask", "question": "q", "selected_lines": ["a"]},
        {"context": "x" * 65, "question": "q"},
    ):
        response = client.post("/v1/context", json=body, headers=AUTH)
        assert response.status_code in {400, 404}, body
        assert response.json()["error"]["type"] in {"invalid_request_error", "not_found_error"}


def test_a_punctuation_heavy_question_is_not_a_500(client: TestClient) -> None:
    response = client.post("/v1/context", json={"context": "ask", "question": "{}();" * 400}, headers=AUTH)
    assert response.status_code == 200


# Other routes, auth and the Host guard.


def test_rag_detail_and_listing(client: TestClient) -> None:
    detail = client.get("/v1/rags/docs", headers=AUTH).json()
    assert detail["documents"] >= 1
    assert detail["manifest"]["dims"] == 384
    assert detail["manifest"]["embedder"] == "bge-small-en-v1.5"
    assert client.get("/v1/rags/ghost", headers=AUTH).status_code == 404
    assert {item["name"] for item in client.get("/v1/rags", headers=AUTH).json()["rags"]} >= {
        "docs",
        "manuals",
        "mixed",
    }


def test_unknown_rag_on_each_route_is_a_404(client: TestClient) -> None:
    assert client.delete("/v1/rags/ghost/documents/x", headers=AUTH).status_code == 404
    assert client.post("/v1/rags/ghost/search", json={"question": "q"}, headers=AUTH).status_code == 404
    assert client.post("/v1/rags/ghost/rebuild", headers=AUTH).status_code == 404


def test_wrong_method_and_bad_json_use_the_error_body(client: TestClient) -> None:
    wrong = client.get("/v1/context", headers=AUTH)
    assert wrong.status_code == 405
    assert wrong.json()["error"]["type"] == "invalid_request_error"
    bad = client.post("/v1/context", content=b"{bad", headers={**AUTH, "Content-Type": "application/json"})
    assert bad.status_code == 400
    assert "detail" not in bad.json()
    assert client.get("/v1/prompts/helper/not-a-number", headers=AUTH).status_code == 400
    assert client.post("/v1/tokens/count", json={"text": "x", "generator": "gpt-x"}, headers=AUTH).status_code == 400
    assert client.post("/v1/tokens/count", json={"text": ""}, headers=AUTH).json()["tokens"] == 0


@pytest.mark.parametrize(
    "authorization",
    [
        KEY,
        f"bearer {KEY}",
        f"Bearer {KEY}x",
        f"Bearer {KEY[:-1]}",
        f"Bearer  {KEY}",
        f"Basic {KEY}",
        "",
        f"Bearer {KEY} ",
    ],
)
def test_only_the_exact_bearer_key_is_accepted(client: TestClient, authorization: str) -> None:
    response = client.get("/v1/rags", headers={"Authorization": authorization})
    assert response.status_code == 401
    assert response.json()["error"]["type"] == "authentication_error"


@pytest.mark.parametrize(
    ("host", "status"),
    [
        ("127.0.0.1", 200),
        ("127.0.0.1:5000", 200),
        ("localhost:80", 200),
        ("[::1]:8000", 200),
        ("127.0.0.1.evil.com", 400),
        ("evil.com", 400),
        ("evil.com:127", 400),
        ("0.0.0.0", 400),
        ("[::2]:80", 400),
    ],
)
def test_the_host_header_must_be_a_loopback_name(client: TestClient, host: str, status: int) -> None:
    assert client.get("/health/live", headers={"Host": host}).status_code == status


def test_a_browser_origin_is_refused_also_on_the_open_route(client: TestClient) -> None:
    assert client.get("/health/live", headers={"Origin": "http://localhost:3000"}).status_code == 400
    assert client.get("/v1/rags", headers={**AUTH, "Origin": "null"}).status_code == 400
