"""Start the real service in a child process, with the hardening, and call it over loopback."""

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from service_setup import MODELS, kept_environment, write_setup

pytestmark = [
    pytest.mark.skipif(not (MODELS / "bge-small-en-v1.5" / "model.onnx").exists(), reason="models not fetched"),
    pytest.mark.allow_hosts(["127.0.0.1"]),
]


def _get(url: str, path: str) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(url + path)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_service_starts_hardened_and_stops_when_input_closes(tmp_path: Path) -> None:
    env = {
        **kept_environment(),
        **write_setup(tmp_path),
        "XatRag__Urls": "http://127.0.0.1:0",
        "XatRag__ReadyFile": str(tmp_path / "ready.json"),
        "XatRag__StopWhenInputCloses": "true",
        "LANGSMITH_TRACING": "true",
        "LANGSMITH_API_KEY": "fake-key",
        "OPENAI_API_KEY": "fake-key",
    }
    service = subprocess.Popen(
        [sys.executable, "-m", "xat_rag_service.main"],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        ready = tmp_path / "ready.json"
        for _ in range(600):
            if ready.exists() or service.poll() is not None:
                break
            time.sleep(0.1)
        assert ready.exists(), service.stderr.read().decode() if service.stderr else ""
        info = json.loads(ready.read_text())
        url = info["url"]
        assert url.startswith("http://127.0.0.1:")
        if sys.platform != "win32":  # On Windows, the Python of a virtual environment starts a second process.
            assert info["pid"] == service.pid
        assert _get(url, "/health/live")[0] == 200
        status, body = _get(url, "/health/ready")
        assert status == 200, body
        status, body = _get(url, "/v1/rags")
        assert status == 200
        assert {rag["name"] for rag in body["rags"]} == {"docs", "manuals"}  # type: ignore[union-attr]
        before = ready.read_bytes()
        second = subprocess.run(
            [sys.executable, "-m", "xat_rag_service.main"], env=env, capture_output=True, timeout=120, check=False
        )
        assert second.returncode == 2
        assert b"Another service uses the data folder" in second.stderr
        assert ready.read_bytes() == before  # A second start must not delete the file of the running service.
    finally:
        assert service.stdin is not None
        service.stdin.close()
        try:
            service.wait(timeout=30)
        except subprocess.TimeoutExpired:
            service.kill()
            raise
    assert service.returncode == 0
    assert not (tmp_path / "ready.json").exists()
