"""Milestone 5 tests of the real service process: a Unix socket listener and startup errors."""

import http.client
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from service_setup import MODELS, kept_environment, write_setup

pytestmark = [
    pytest.mark.skipif(not (MODELS / "bge-small-en-v1.5" / "model.onnx").exists(), reason="models not fetched"),
    pytest.mark.allow_hosts(["127.0.0.1"]),
]


class UnixConnection(http.client.HTTPConnection):
    """An HTTP connection over a Unix socket."""

    def __init__(self, path: str) -> None:
        super().__init__("localhost")
        self._path = path

    def connect(self) -> None:
        assert sys.platform != "win32"  # Windows has no AF_UNIX. This line also tells pyright the platform.
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(30)
        self.sock.connect(self._path)


def base_env(folder: Path) -> dict[str, str]:
    return {**kept_environment(), **write_setup(folder)}


def run_once(env: dict[str, str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "xat_rag_service.main"], env=env, capture_output=True, timeout=120, check=False
    )


@pytest.fixture
def short_folder() -> Iterator[Path]:
    # The path of a Unix socket must be short (104 bytes on macOS).
    folder = Path(tempfile.mkdtemp(prefix="xr"))
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


@pytest.mark.skipif(sys.platform == "win32", reason="Unix sockets")
def test_the_service_listens_on_a_unix_socket(tmp_path: Path, short_folder: Path) -> None:
    socket_path = short_folder / "s.sock"
    ready = tmp_path / "ready.json"
    env = {
        **base_env(tmp_path),
        "XatRag__Urls": f"http://unix:{socket_path}",
        "XatRag__ReadyFile": str(ready),
        "XatRag__StopWhenInputCloses": "true",
    }
    service = subprocess.Popen(
        [sys.executable, "-m", "xat_rag_service.main"],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        for _ in range(600):
            if ready.exists() or service.poll() is not None:
                break
            time.sleep(0.1)
        assert ready.exists(), service.stderr.read().decode() if service.stderr else ""
        assert json.loads(ready.read_text())["url"] == f"http://unix:{socket_path}"
        assert socket_path.exists()
        connection = UnixConnection(str(socket_path))
        connection.request("GET", "/health/live")
        response = connection.getresponse()
        assert (response.status, json.loads(response.read())) == (200, {"status": "live"})
        connection.close()
        connection = UnixConnection(str(socket_path))
        connection.request("GET", "/v1/rags")
        response = connection.getresponse()
        assert response.status == 200
        assert {rag["name"] for rag in json.loads(response.read())["rags"]} == {"docs", "manuals"}
        connection.close()
        # A client that still sends a key is not refused.
        connection = UnixConnection(str(socket_path))
        connection.request("GET", "/v1/rags", headers={"Authorization": "Bearer stale"})
        assert connection.getresponse().status == 200
        connection.close()
    finally:
        assert service.stdin is not None
        service.stdin.close()
        try:
            service.wait(timeout=30)
        except subprocess.TimeoutExpired:
            service.kill()
            raise
    assert service.returncode == 0
    assert not ready.exists()


def test_a_missing_data_folder_setting_stops_the_start_with_a_message(tmp_path: Path) -> None:
    env = base_env(tmp_path)
    del env["XatRag__DataFolder"]
    result = run_once(env)
    assert result.returncode != 0
    assert b"set XatRag__DataFolder" in result.stderr
    assert b"Traceback" not in result.stderr


def test_bad_number_settings_stop_the_start_with_exit_code_2(tmp_path: Path) -> None:
    result = run_once({**base_env(tmp_path), "XatRag__QueueLimit": "many"})
    assert result.returncode == 2
    assert b"QueueLimit" in result.stderr
    assert b"Traceback" not in result.stderr


def test_a_non_loopback_address_stops_the_start_with_exit_code_2(tmp_path: Path) -> None:
    result = run_once({**base_env(tmp_path), "XatRag__Urls": "http://0.0.0.0:8080"})
    assert result.returncode == 2
    assert b"XatRag__Urls" in result.stderr
    assert b"Traceback" not in result.stderr


@pytest.mark.parametrize("stale_key", [False, True], ids=["no key", "stale XatRag__AccessKey"])
def test_the_service_starts_with_no_access_key_and_ignores_a_stale_one(tmp_path: Path, stale_key: bool) -> None:
    ready = tmp_path / "ready.json"
    stale_value = "stale-key-0123456789"
    env = {
        **base_env(tmp_path),
        "XatRag__Urls": "http://127.0.0.1:0",
        "XatRag__ReadyFile": str(ready),
        "XatRag__StopWhenInputCloses": "true",
    }
    assert "XatRag__AccessKey" not in env
    if stale_key:
        env["XatRag__AccessKey"] = stale_value
    service = subprocess.Popen(
        [sys.executable, "-m", "xat_rag_service.main"],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        for _ in range(600):
            if ready.exists() or service.poll() is not None:
                break
            time.sleep(0.1)
        assert ready.exists(), service.stderr.read().decode() if service.stderr else ""
        url = json.loads(ready.read_text())["url"]
        with urllib.request.urlopen(f"{url}/v1/rags", timeout=30) as response:
            assert response.status == 200
    finally:
        assert service.stdin is not None
        service.stdin.close()
        try:
            output, errors = service.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            service.kill()
            raise
    assert service.returncode == 0
    assert b"AccessKey" not in errors
    assert stale_value.encode() not in output + errors


def test_a_bad_recipe_stops_the_start_with_exit_code_2(tmp_path: Path) -> None:
    env = base_env(tmp_path)
    (tmp_path / "recipes" / "rags" / "docs.toml").write_text('[rag]\nname = "docs"\nunknown = 1\n')
    result = run_once(env)
    assert result.returncode == 2
    assert b"docs.toml" in result.stderr
    assert b"Traceback" not in result.stderr


def test_an_invalid_port_stops_the_start_with_exit_code_2(tmp_path: Path) -> None:
    result = run_once({**base_env(tmp_path), "XatRag__Urls": "http://127.0.0.1:99999"})
    assert result.returncode == 2
    assert b"Traceback" not in result.stderr
