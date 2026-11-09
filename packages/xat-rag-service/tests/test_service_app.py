import io
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from service_setup import MODELS, multipart, write_setup
from xat_rag import Settings, XatRag
from xat_rag_service.app import create_app

pytestmark = [
    pytest.mark.skipif(not (MODELS / "bge-small-en-v1.5" / "model.onnx").exists(), reason="models not fetched"),
    # On Windows, the event loop of the test client makes its internal socket pair over loopback.
    pytest.mark.allow_hosts(["127.0.0.1"]),
]
DOC = b"# Handbook\n\n## Orders\n\nThe orders service runs on port 8080.\n\n## Food\n\nPasta on Friday.\n"
HOST_MESSAGE = "The Host header must be a loopback name."
ORIGIN_MESSAGE = "Requests from browsers are refused."


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    folder = tmp_path_factory.mktemp("svc")
    env = write_setup(folder)
    rag = XatRag.open(
        Settings(
            data_folder=Path(env["XatRag__DataFolder"]),
            recipes_folder=Path(env["XatRag__RecipesFolder"]),
            models_folder=MODELS,
            lock_file=Path(env["XatRag__LockFile"]),
            threads=1,
        )
    )
    return TestClient(create_app(rag, queue_limit=4, version="test"), base_url="http://127.0.0.1")


def _wait(client: TestClient, job_id: str) -> dict[str, object]:
    for _ in range(200):
        job = client.get(f"/v1/jobs/{job_id}").json()
        if job["status"] in {"done", "failed", "cancelled"}:
            return job
        time.sleep(0.05)
    raise AssertionError("the job did not end")


def test_access_control(client: TestClient) -> None:
    assert client.get("/health/live").status_code == 200
    assert client.get("/v1/rags").status_code == 200
    # A client that still sends a key is not refused.
    assert client.get("/v1/rags", headers={"Authorization": "Bearer x"}).status_code == 200
    assert client.get("/v1/rags", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/v1/rags", headers={"Origin": "http://evil.example"}).status_code == 400


def test_bad_host_with_origin_returns_the_host_message(client: TestClient) -> None:
    response = client.get("/v1/rags", headers={"Host": "evil.example", "Origin": "http://evil.example"})
    assert response.status_code == 400
    assert response.json() == {"error": {"message": HOST_MESSAGE, "type": "invalid_request_error"}}


def test_origin_is_refused_on_every_route_without_authorization(client: TestClient) -> None:
    headers = {"Origin": "http://evil.example"}
    expected = {"error": {"message": ORIGIN_MESSAGE, "type": "invalid_request_error"}}
    responses = [
        client.get("/v1/rags", headers=headers),
        client.get("/v1/rags/docs", headers=headers),
        client.get("/v1/jobs/none", headers=headers),
        client.post("/v1/context", json={"context": "ask", "question": "q"}, headers=headers),
        client.post("/v1/query/build", json={"question": "q"}, headers=headers),
        client.post("/v1/rags/docs/search", json={"question": "q"}, headers=headers),
        client.put("/v1/rags/docs/documents", content=b"", headers=headers),
        client.delete("/v1/rags/docs/documents/x", headers=headers),
        client.post("/v1/rags/docs/rebuild", headers=headers),
        client.get("/v1/nothing", headers=headers),
    ]
    for response in responses:
        assert response.status_code == 400
        assert response.json() == expected


def test_health_live_passes_host_and_origin_checks(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "live"}
    assert client.get("/health/live", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/health/live", headers={"Origin": "http://evil.example"}).status_code == 400
    assert client.get("/health/live", headers={"Host": "localhost:8080"}).status_code == 200


def test_ingest_and_context(client: TestClient) -> None:
    fields = json.dumps(
        [
            {
                "doc_id": "docs:handbook.md",
                "kind": "markdown",
                "part": "f1",
                "uri": "docs/handbook.md",
                "title": "Handbook",
                "version": "v9",
                "metadata": {"team": "core"},
            }
        ]
    )
    body, content_type = multipart(fields, {"f1": DOC})
    response = client.put("/v1/rags/docs/documents", content=body, headers={"Content-Type": content_type})
    assert response.status_code == 202, response.text
    job = _wait(client, response.json()["job_id"])
    assert job["status"] == "done"
    assert job["results"][0]["status"] == "created"  # type: ignore[index]

    result = client.post("/v1/context", json={"context": "ask", "question": "Which port do orders use?"})
    assert result.status_code == 200, result.text
    data = result.json()
    assert set(data) == {"chunks", "omitted", "debug"}
    top = data["chunks"][0]
    assert top["chunk_id"] == "docs:handbook.md#h/handbook/orders"
    assert top["source"]["span"]["type"] == "line"
    assert top["source"]["uri"] == "docs/handbook.md"
    assert top["source"]["title"] == "Handbook"
    assert top["source"]["version"] == "v9"
    assert top["metadata"]["team"] == "core"
    assert "8080" in top["text"]


def test_errors_do_not_echo_input(client: TestClient) -> None:
    secret = "SECRET-INPUT-VALUE"
    response = client.post("/v1/context", json={"context": "ask", "question": 5, "x": secret})
    assert response.status_code == 400
    assert secret not in response.text
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert client.post("/v1/context", json={"context": "missing", "question": "q"}).status_code == 404
    assert client.get("/v1/nothing").json()["error"]["type"] == "not_found_error"
    big = client.post("/v1/context", content=b"x" * (2 * 1024 * 1024), headers={"Content-Type": "application/json"})
    assert big.status_code == 413


def test_query_build_and_delete(client: TestClient) -> None:
    query = client.post("/v1/query/build", json={"question": "ERROR PROJ-7 failed in OrderService.find"})
    assert query.status_code == 200
    data = query.json()
    assert set(data) == {"lexical_terms", "dense_text", "rerank_text"}
    assert "PROJ-7" in data["lexical_terms"]
    assert "OrderService.find" in data["lexical_terms"]
    assert data["dense_text"] == "ERROR PROJ-7 failed in OrderService.find"
    old = client.post("/v1/query/build", json={"builder": "log-v1", "log_lines": ["ERROR PROJ-7 failed"]})
    assert old.status_code == 400
    fields = json.dumps([{"doc_id": "docs:tmp/a b.md", "kind": "markdown", "part": "f", "uri": ""}])
    body, content_type = multipart(fields, {"f": b"# T\n\ntemp"})
    job = client.put("/v1/rags/docs/documents", content=body, headers={"Content-Type": content_type}).json()
    _wait(client, job["job_id"])
    assert client.delete("/v1/rags/docs/documents/docs%3Atmp%2Fa%20b.md").json() == {"deleted": True}
    assert client.delete("/v1/rags/docs/documents/docs%3Atmp%2Fa%20b.md").status_code == 404


def test_scanned_pdf_report_in_job(client: TestClient) -> None:
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    buffer = io.BytesIO()
    writer.write(buffer)
    fields = json.dumps([{"doc_id": "manuals:scan.pdf", "kind": "pdf", "part": "p"}])
    body, content_type = multipart(fields, {"p": buffer.getvalue()})
    job = _wait(
        client,
        client.put("/v1/rags/manuals/documents", content=body, headers={"Content-Type": content_type}).json()["job_id"],
    )
    result = job["results"][0]  # type: ignore[index]
    assert result["status"] == "failed"
    assert result["reason"] == "no_text_layer"
