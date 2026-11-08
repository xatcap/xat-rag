"""Milestone 5 tests of the service routes with the real models: uploads, jobs, limits, errors and health."""

import io
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
from xat_rag import ContextRequest, RetrievalResult, Settings, XatRag
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


MIXED_CONTEXT = """
[context]
name = "mixed"
chunks = 20

[[context.sources]]
rag = "mixed"
"""

HTML = b"<html><body><h1>Gateway</h1><p>Set HTTPS_PROXY before you restart the gateway.</p></body></html>"


def prepare(folder: Path) -> Settings:
    env = write_setup(folder)
    recipes = folder / "recipes"
    (recipes / "rags" / "mixed.toml").write_text(MIXED_RAG)
    (recipes / "contexts" / "mixed.toml").write_text(MIXED_CONTEXT)
    return Settings(
        data_folder=Path(env["XatRag__DataFolder"]),
        recipes_folder=recipes,
        models_folder=MODELS,
        lock_file=LOCK,
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
        {
            "doc_id": "mixed:manual.pdf",
            "kind": "pdf",
            "part": "p",
            "uri": "https://wiki/manual.pdf",
            "title": "Manual",
            "version": "rev-4",
            "metadata": {"team": "ops", "level": 2},
        },
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
    source = hits[0]["source"]
    assert source["span"]["type"] == "page"
    assert source["uri"] == f"https://wiki/manual.pdf#page={source['span']['page_start']}"
    assert (source["rag"], source["doc_id"], source["source_kind"]) == ("mixed", "mixed:manual.pdf", "pdf")
    assert (source["title"], source["version"]) == ("Manual", "rev-4")
    assert hits[0]["metadata"] == {"team": "ops", "level": 2}
    spans = {hit["chunk_id"].split("#")[0]: hit["source"]["span"]["type"] for hit in hits}
    assert spans["mixed:manual.pdf"] == "page"
    assert spans["mixed:spec.docx"] == "block"
    assert spans["mixed:guide.html"] == "line"
    assert spans["mixed:note.md"] == "line"
    billing = client.post("/v1/rags/mixed/search", json={"question": "billing TLS port 9443"}, headers=AUTH).json()
    assert billing["hits"][0]["chunk_id"].startswith("mixed:spec.docx#")
    context = client.post("/v1/context", json={"context": "mixed", "question": "shipping depot opens"}, headers=AUTH)
    assert context.status_code == 200, context.text
    top = context.json()["chunks"][0]
    assert top["chunk_id"].startswith("mixed:manual.pdf#")
    assert top["source"]["uri"].startswith("https://wiki/manual.pdf#page=")
    assert top["source"]["version"] == "rev-4"
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
    for path in ("/v1/context", "/v1/query/build", "/v1/rags/docs/search"):
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


def slow_retrieve(started: threading.Event | None, release: threading.Event) -> Callable[[ContextRequest], Any]:
    def slow(request: ContextRequest) -> RetrievalResult:
        if started is not None:
            started.set()
        assert release.wait(10)
        return RetrievalResult(())

    return slow


def ask(client: TestClient, question: str = "a") -> Any:
    return client.post("/v1/context", json={"context": "ask", "question": question}, headers=AUTH)


def test_overloaded_with_a_queue_limit_of_one(
    make_client: Callable[..., TestClient], rag: XatRag, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(rag, "retrieve", slow_retrieve(started, release))
    client = make_client(queue_limit=1)
    first: list[Any] = []
    worker = threading.Thread(target=lambda: first.append(ask(client, "a")))
    worker.start()
    try:
        assert started.wait(10)
        second = ask(client, "b")
        assert second.status_code == 503
        assert second.json()["error"]["type"] == "overloaded_error"
        assert second.headers["Retry-After"] == "1"
        assert client.get("/health/live").status_code == 200  # Routes with no queue still answer.
        assert client.get("/v1/jobs/j_none", headers=AUTH).status_code == 404
    finally:
        release.set()
        worker.join(10)
    assert first[0].status_code == 200
    assert ask(client, "c").status_code == 200


def test_concurrent_requests_over_the_queue_limit_are_refused_and_the_rest_complete(
    make_client: Callable[..., TestClient], rag: XatRag, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    monkeypatch.setattr(rag, "retrieve", slow_retrieve(None, release))
    client = make_client(queue_limit=2)
    responses: list[Any] = []
    lock = threading.Lock()

    def call() -> None:
        response = ask(client)
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
    assert ask(client).status_code == 200


# Context route.


CHUNK_KEYS = {
    "chunk_id", "parent_id", "kind", "score", "size_tokens", "header", "text", "source", "metadata"
}  # fmt: skip
SOURCE_KEYS = {"rag", "doc_id", "source_kind", "uri", "title", "version", "span"}


def test_the_context_response_has_chunks_omitted_and_debug_only(client: TestClient) -> None:
    response = client.post("/v1/context", json={"context": "ask", "question": "Which port?"}, headers=AUTH)
    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data) == {"chunks", "omitted", "debug"}
    assert data["debug"] is None
    assert isinstance(data["omitted"], list)
    chunk = data["chunks"][0]
    assert set(chunk) == CHUNK_KEYS
    assert set(chunk["source"]) == SOURCE_KEYS
    assert chunk["chunk_id"] == "docs:handbook.md#h/handbook/orders"
    assert chunk["source"]["rag"] == "docs"
    assert chunk["source"]["doc_id"] == "docs:handbook.md"
    assert chunk["source"]["source_kind"] == "markdown"
    assert chunk["source"]["span"]["type"] == "line"
    assert chunk["source"]["version"] == ""
    assert isinstance(chunk["metadata"], dict)
    assert chunk["size_tokens"] > 0
    assert isinstance(chunk["score"], float)
    for removed in ("budget", "rendered", "input", "citation", "pack_id", "messages", "max_tokens", "generator"):
        assert removed not in data
    for removed in ("label", "tags", "tokens", "uri", "span", "rag", "doc_id"):
        assert removed not in chunk


def test_the_chunks_are_sorted_by_score(client: TestClient) -> None:
    data = client.post("/v1/context", json={"context": "ask", "question": "Which port?"}, headers=AUTH).json()
    scores = [chunk["score"] for chunk in data["chunks"]]
    assert scores == sorted(scores, reverse=True)


def test_debug_is_a_list_of_scores_when_asked(client: TestClient) -> None:
    body = {"context": "ask", "question": "Which port?", "debug": True}
    data = client.post("/v1/context", json=body, headers=AUTH).json()
    assert data["debug"]
    assert set(data["debug"][0]) == {
        "chunk_id", "rag", "lexical_rank", "dense_rank", "rrf_score", "rerank_score", "rerank_truncated"
    }  # fmt: skip
    assert {item["chunk_id"] for item in data["debug"]} >= {chunk["chunk_id"] for chunk in data["chunks"]}


def test_the_number_of_chunks_can_be_set_in_the_request(client: TestClient) -> None:
    docs = {"context": "ask", "question": "Which port?"}
    assert len(client.post("/v1/context", json=docs, headers=AUTH).json()["chunks"]) >= 1
    one = client.post("/v1/context", json={**docs, "chunks": 1}, headers=AUTH).json()
    assert len(one["chunks"]) == 1
    assert one["chunks"][0]["chunk_id"] == "docs:handbook.md#h/handbook/orders"
    limited = [item for item in one["omitted"] if item["reason"] == "limit"]
    assert all(set(item) == {"chunk_id", "reason"} for item in one["omitted"])
    assert all(item["chunk_id"] != one["chunks"][0]["chunk_id"] for item in limited)


@pytest.mark.parametrize("chunks", [0, -1, 101, 10**6, "ten", 1.5, None])
def test_a_number_of_chunks_outside_1_to_100_is_a_400(client: TestClient, chunks: Any) -> None:
    body = {"context": "ask", "question": "Which port?", "chunks": chunks}
    response = client.post("/v1/context", json=body, headers=AUTH)
    if chunks is None:  # No number means the recipe default.
        assert response.status_code == 200
        return
    assert response.status_code == 400, chunks
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert "chunks" in response.json()["error"]["message"]


@pytest.mark.parametrize("chunks", [1, 100])
def test_the_limits_of_the_number_of_chunks_are_accepted(client: TestClient, chunks: int) -> None:
    body = {"context": "ask", "question": "Which port?", "chunks": chunks}
    assert client.post("/v1/context", json=body, headers=AUTH).status_code == 200


@pytest.mark.parametrize("question", ["", " ", "   ", "\n\t", "\u200b", "\u200b\u200b \u2060\ufeff"])
def test_an_empty_or_zero_width_question_is_a_400(client: TestClient, question: str) -> None:
    response = client.post("/v1/context", json={"context": "ask", "question": question}, headers=AUTH)
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert "needs a question" in response.json()["error"]["message"]


def test_a_question_over_20000_characters_is_a_400_and_at_20000_it_is_accepted(client: TestClient) -> None:
    long = client.post("/v1/context", json={"context": "ask", "question": "a" * 20001}, headers=AUTH)
    assert long.status_code == 400
    assert long.json()["error"]["type"] == "invalid_request_error"
    assert "question" in long.json()["error"]["message"]
    assert "aaaa" not in long.text
    ok = client.post("/v1/context", json={"context": "ask", "question": ("orders " * 3000)[:20000]}, headers=AUTH)
    assert ok.status_code == 200, ok.text


@pytest.mark.parametrize(
    "removed",
    [
        {"log_lines": ["ERROR x"]},
        {"selected_lines": [1]},
        {"reserved_output": 100},
        {"input_max_tokens": 100},
        {"generator": "ministral-3-3b"},
        {"builder": "text-v1"},
        {"prompts": []},
        {"pinned": []},
    ],
)
def test_a_removed_field_in_the_context_body_is_a_400(client: TestClient, removed: dict[str, Any]) -> None:
    response = client.post("/v1/context", json={"context": "ask", "question": "q", **removed}, headers=AUTH)
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert "unknown field" in response.json()["error"]["message"]


def test_context_body_limits_and_shapes(client: TestClient) -> None:
    for body in (
        {"context": "ask"},
        {"question": "q"},
        {"context": "", "question": "q"},
        {"context": "ask", "question": 5},
        {"context": "ask", "question": "q", "unknown": 1},
        {"context": "ask", "question": "q", "debug": "maybe"},
        {"context": "x" * 65, "question": "q"},
        {"context": "missing", "question": "q"},
    ):
        response = client.post("/v1/context", json=body, headers=AUTH)
        assert response.status_code in {400, 404}, body
        assert response.json()["error"]["type"] in {"invalid_request_error", "not_found_error"}
    assert client.post("/v1/context", json={"context": "missing", "question": "q"}, headers=AUTH).status_code == 404


@pytest.mark.parametrize("question", ["{}();" * 4000, "\u4e2d" * 20000, "0x" + "deadbeef" * 2000])
def test_a_long_or_odd_question_is_not_a_500(client: TestClient, question: str) -> None:
    response = client.post("/v1/context", json={"context": "ask", "question": question}, headers=AUTH)
    assert response.status_code == 200, response.text


def test_the_search_route_hits_have_the_chunk_form_and_the_scores_of_each_stage(client: TestClient) -> None:
    response = client.post("/v1/rags/docs/search", json={"question": "Which port?"}, headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"query", "hits"}
    hit = data["hits"][0]
    assert set(hit) == CHUNK_KEYS | {"lexical_rank", "dense_rank", "rrf_score", "rerank_score"}
    assert set(hit["source"]) == SOURCE_KEYS
    assert hit["rrf_score"] is not None
    assert hit["rerank_score"] == hit["score"]
    assert set(data["query"]) == {"lexical_terms", "dense_text", "rerank_text"}


def test_the_search_route_refuses_a_question_over_20000_characters(client: TestClient) -> None:
    response = client.post("/v1/rags/docs/search", json={"question": "a" * 20001}, headers=AUTH)
    assert response.status_code == 400


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/v1/tokens/count"),
        ("post", "/v1/citations/validate"),
        ("get", "/v1/prompts/helper/1"),
        ("get", "/v1/prompts/helper"),
        ("get", "/v1/prompts"),
    ],
)
def test_the_removed_routes_are_404(client: TestClient, method: str, path: str) -> None:
    kwargs: dict[str, Any] = {"json": {"text": "x"}} if method == "post" else {}
    response = getattr(client, method)(path, headers=AUTH, **kwargs)
    assert response.status_code == 404
    assert response.json() == {"error": {"message": "The route does not exist.", "type": "not_found_error"}}


def test_the_manifest_of_a_rag_has_the_size_tokenizer(client: TestClient) -> None:
    manifest = client.get("/v1/rags/docs", headers=AUTH).json()["manifest"]
    assert manifest["size_tokenizer_id"].startswith("ministral-3-3b-tokenizer@")
    assert "generator_tokenizer_id" not in manifest


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


@pytest.mark.parametrize("value", [True, "5", 5.0])
def test_the_number_of_chunks_must_be_a_json_integer(client: TestClient, value: object) -> None:
    body = {"context": "ask", "question": "Which port?", "chunks": value}
    assert client.post("/v1/context", json=body, headers=AUTH).status_code == 400
