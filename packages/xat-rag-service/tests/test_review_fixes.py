"""Regression tests for the findings of the M4 and M5 review."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from service_setup import KEY, MODELS, multipart, write_setup
from xat_rag import Settings, XatRag
from xat_rag_core.errors import ConfigError, InvalidRequestError
from xat_rag_service.app import create_app
from xat_rag_service.uploads import read_upload

pytestmark = [
    pytest.mark.skipif(not (MODELS / "bge-small-en-v1.5" / "model.onnx").exists(), reason="models not fetched"),
    # On Windows, the event loop of the test client makes its internal socket pair over loopback.
    pytest.mark.allow_hosts(["127.0.0.1"]),
]
AUTH = {"Authorization": f"Bearer {KEY}"}


def _settings(env: dict[str, str]) -> Settings:
    return Settings(
        data_folder=Path(env["XatRag__DataFolder"]),
        recipes_folder=Path(env["XatRag__RecipesFolder"]),
        models_folder=MODELS,
        lock_file=Path(env["XatRag__LockFile"]),
        threads=1,
    )


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    env = write_setup(tmp_path_factory.mktemp("review"))
    return TestClient(
        create_app(XatRag.open(_settings(env)), KEY, queue_limit=4, version="t"), base_url="http://127.0.0.1"
    )


def test_upload_parser_limits_and_shape() -> None:
    body, content_type = multipart(json.dumps([]), {f"f{i}": b"x" for i in range(300)})
    with pytest.raises(InvalidRequestError, match="at most 256"):
        read_upload(content_type, body)
    with pytest.raises(InvalidRequestError, match="closing boundary"):
        read_upload(
            "multipart/form-data; boundary=b", b'--b\r\nContent-Disposition: form-data; name="documents"\r\n\r\n[]'
        )
    deep, deep_type = multipart("[" * 100000, {})
    with pytest.raises(InvalidRequestError, match="valid list"):
        read_upload(deep_type, deep)


def test_long_questions_are_not_cut_and_never_a_500(client: TestClient) -> None:
    for question in ("0x" + "deadbeef" * 300, "設定" * 900, "com.shop.Order$Inner.find(" * 70):
        response = client.post("/v1/context", json={"context": "ask", "question": question[:20000]}, headers=AUTH)
        assert response.status_code == 200, response.text
    longest = client.post("/v1/context", json={"context": "ask", "question": "設" * 20000}, headers=AUTH)
    assert longest.status_code == 200, longest.text
    too_long = client.post("/v1/context", json={"context": "ask", "question": "設" * 20001}, headers=AUTH)
    assert too_long.status_code == 400, too_long.text


def test_unknown_field_names_are_not_echoed(client: TestClient) -> None:
    response = client.post("/v1/context", json={"context": "ask", "question": "q", "zzSECRETkey": 1}, headers=AUTH)
    assert response.status_code == 400
    assert "zzSECRETkey" not in response.text
    assert "unknown field" in response.text
    assert "zz-job" not in client.get("/v1/jobs/zz-job", headers=AUTH).text


def test_host_check_ignores_case(client: TestClient) -> None:
    assert client.get("/v1/rags", headers={**AUTH, "Host": "LOCALHOST:1"}).status_code == 200


def test_rebuild_is_refused_while_a_job_runs(client: TestClient) -> None:
    fields = json.dumps([{"doc_id": f"docs:{i}.md", "kind": "markdown", "part": f"f{i}"} for i in range(30)])
    body, content_type = multipart(fields, {f"f{i}": f"# T{i}\n\n{'word ' * 300}".encode() for i in range(30)})
    job = client.put("/v1/rags/docs/documents", content=body, headers={**AUTH, "Content-Type": content_type}).json()
    response = client.post("/v1/rags/docs/rebuild", headers=AUTH)
    assert response.status_code == 400
    assert "job" in response.json()["error"]["message"]
    for _ in range(400):
        if client.get(f"/v1/jobs/{job['job_id']}", headers=AUTH).json()["status"] == "done":
            break
        time.sleep(0.05)
    assert client.post("/v1/rags/docs/rebuild", headers=AUTH).status_code == 200


def test_recipe_errors_stop_the_start(tmp_path: Path) -> None:
    env = write_setup(tmp_path)
    context = tmp_path / "recipes" / "contexts" / "ask.toml"
    context.write_text(context.read_text().replace('name = "ask"', 'name = "ask"\npinned = ["prompt:missing@1"]'))
    with pytest.raises(ConfigError, match=r"context\.pinned"):
        XatRag.open(_settings(env))


def test_different_rrf_k_without_a_reranker_stops_the_start(tmp_path: Path) -> None:
    env = write_setup(tmp_path)
    rags = tmp_path / "recipes" / "rags"
    for name, k in (("docs", 60), ("manuals", 20)):
        recipe = (rags / f"{name}.toml").read_text().replace("ms-marco-minilm-l6-v2", "none")
        (rags / f"{name}.toml").write_text(recipe + f"\n[retrieval]\nrrf_k = {k}\n")
    context = tmp_path / "recipes" / "contexts" / "ask.toml"
    context.write_text(context.read_text() + '\n[[context.sources]]\nrag = "manuals"\n')
    with pytest.raises(ConfigError, match="one rrf_k"):
        XatRag.open(_settings(env))


def _start(tmp_path: Path, extra: dict[str, str]) -> tuple[subprocess.Popen[bytes], dict[str, object]]:
    env = {
        **{k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "TMPDIR"}},
        **write_setup(tmp_path),
        "XatRag__ReadyFile": str(tmp_path / "ready.json"),
        **extra,
    }
    process = subprocess.Popen([sys.executable, "-m", "xat_rag_service.main"], env=env, stdin=subprocess.PIPE)
    for _ in range(600):
        if (tmp_path / "ready.json").exists() or process.poll() is not None:
            break
        time.sleep(0.1)
    return process, json.loads((tmp_path / "ready.json").read_text())


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals and sockets")
@pytest.mark.allow_hosts(["127.0.0.1"])
def test_sigterm_runs_the_cleanup(tmp_path: Path) -> None:
    process, info = _start(tmp_path, {"XatRag__Urls": "http://127.0.0.1:0"})
    request = urllib.request.Request(f"{info['url']}/health/live")
    with urllib.request.urlopen(request, timeout=30) as response:
        assert response.status == 200
    assert (tmp_path / "data").stat().st_mode & 0o777 == 0o700
    process.send_signal(signal.SIGTERM)
    assert process.wait(timeout=60) == 0
    assert not (tmp_path / "ready.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX sockets")
def test_unix_socket_is_private(tmp_path: Path) -> None:
    socket_path = Path(tempfile.mkdtemp(prefix="xr", dir="/tmp")) / "x.sock"  # macOS allows 104 characters.
    process, info = _start(
        tmp_path, {"XatRag__Urls": f"http://unix:{socket_path}", "XatRag__StopWhenInputCloses": "true"}
    )
    try:
        assert info["url"] == f"http://unix:{socket_path}"
        assert socket_path.stat().st_mode & 0o777 == 0o600
    finally:
        assert process.stdin is not None
        process.stdin.close()
        process.wait(timeout=60)
