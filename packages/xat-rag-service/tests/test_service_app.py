import io
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from service_setup import KEY, MODELS, multipart, write_setup
from xat_rag import Settings, XatRag
from xat_rag_service.app import create_app

pytestmark = pytest.mark.skipif(not (MODELS / "bge-small-en-v1.5" / "model.onnx").exists(), reason="models not fetched")
AUTH = {"Authorization": f"Bearer {KEY}"}
DOC = b"# Handbook\n\n## Orders\n\nThe orders service runs on port 8080.\n\n## Food\n\nPasta on Friday.\n"


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
            prompts_folder=Path(env["XatRag__PromptsFolder"]),
            threads=1,
        )
    )
    return TestClient(create_app(rag, KEY, queue_limit=4, version="test"), base_url="http://127.0.0.1")


def _wait(client: TestClient, job_id: str) -> dict[str, object]:
    for _ in range(200):
        job = client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
        if job["status"] in {"done", "failed", "cancelled"}:
            return job
        time.sleep(0.05)
    raise AssertionError("the job did not end")


def test_access_control(client: TestClient) -> None:
    assert client.get("/health/live").status_code == 200
    response = client.get("/v1/rags")
    assert response.status_code == 401
    assert response.json() == {
        "error": {"message": "The access key is missing or wrong.", "type": "authentication_error"}
    }
    assert client.get("/v1/rags", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/v1/rags", headers={**AUTH, "Host": "evil.example"}).status_code == 400
    assert client.get("/v1/rags", headers={**AUTH, "Origin": "http://evil.example"}).status_code == 400
    assert client.get("/v1/rags", headers=AUTH).status_code == 200


def test_ingest_context_and_citations(client: TestClient) -> None:
    fields = json.dumps([{"doc_id": "docs:handbook.md", "kind": "markdown", "part": "f1", "uri": "docs/handbook.md"}])
    body, content_type = multipart(fields, {"f1": DOC})
    response = client.put("/v1/rags/docs/documents", content=body, headers={**AUTH, "Content-Type": content_type})
    assert response.status_code == 202, response.text
    job = _wait(client, response.json()["job_id"])
    assert job["status"] == "done"
    assert job["results"][0]["status"] == "created"  # type: ignore[index]

    pack = client.post("/v1/context", json={"context": "ask", "question": "Which port do orders use?"}, headers=AUTH)
    assert pack.status_code == 200, pack.text
    data = pack.json()
    assert data["chunks"][0]["chunk_id"] == "docs:handbook.md#h/handbook/orders"
    assert data["chunks"][0]["span"]["type"] == "line"
    assert data["rendered"]["messages"][0]["role"] == "system"
    assert "C1" in data["citation"]["grammar"]

    chunks = [{"label": c["label"], "chunk_id": c["chunk_id"]} for c in data["chunks"]]
    check = client.post(
        "/v1/citations/validate",
        json={"answer": json.dumps({"answer": "8080", "citations": ["C1"]}), "chunks": chunks},
        headers=AUTH,
    ).json()
    assert check["valid"]
    assert check["citations"][0]["uri"] == "docs/handbook.md"


def test_errors_do_not_echo_input(client: TestClient) -> None:
    secret = "SECRET-INPUT-VALUE"
    response = client.post("/v1/context", json={"context": "ask", "question": 5, "x": secret}, headers=AUTH)
    assert response.status_code == 400
    assert secret not in response.text
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert client.post("/v1/context", json={"context": "missing", "question": "q"}, headers=AUTH).status_code == 404
    assert client.get("/v1/nothing", headers=AUTH).json()["error"]["type"] == "not_found_error"
    big = client.post(
        "/v1/tokens/count", content=b"x" * (2 * 1024 * 1024), headers={**AUTH, "Content-Type": "application/json"}
    )
    assert big.status_code == 413


def test_query_tokens_prompts_and_delete(client: TestClient) -> None:
    query = client.post(
        "/v1/query/build", json={"builder": "log-v1", "log_lines": ["ERROR PROJ-7 failed"]}, headers=AUTH
    )
    assert {"field": "jira_key", "value": "PROJ-7"} in query.json()["exact_lookups"]
    assert client.post("/v1/tokens/count", json={"text": "Hello world."}, headers=AUTH).json()["tokens"] == 3
    assert client.get("/v1/prompts/helper/1", headers=AUTH).json()["text"].startswith("You answer")
    assert client.get("/v1/prompts/helper/2", headers=AUTH).status_code == 404
    fields = json.dumps([{"doc_id": "docs:tmp/a b.md", "kind": "markdown", "part": "f", "uri": ""}])
    body, content_type = multipart(fields, {"f": b"# T\n\ntemp"})
    job = client.put("/v1/rags/docs/documents", content=body, headers={**AUTH, "Content-Type": content_type}).json()
    _wait(client, job["job_id"])
    assert client.delete("/v1/rags/docs/documents/docs%3Atmp%2Fa%20b.md", headers=AUTH).json() == {"deleted": True}
    assert client.delete("/v1/rags/docs/documents/docs%3Atmp%2Fa%20b.md", headers=AUTH).status_code == 404


def test_scanned_pdf_report_in_job(client: TestClient) -> None:
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    buffer = io.BytesIO()
    writer.write(buffer)
    fields = json.dumps([{"doc_id": "manuals:scan.pdf", "kind": "pdf", "part": "p"}])
    body, content_type = multipart(fields, {"p": buffer.getvalue()})
    job = _wait(
        client,
        client.put("/v1/rags/manuals/documents", content=body, headers={**AUTH, "Content-Type": content_type}).json()[
            "job_id"
        ],
    )
    result = job["results"][0]  # type: ignore[index]
    assert result["status"] == "failed"
    assert result["reason"] == "no_text_layer"
