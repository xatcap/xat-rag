"""Release tasks of xat-rag.

uv run python build/release.py package       # build the frozen service folder with PyInstaller
uv run python build/release.py smoke-test    # start the frozen service and check it end to end
"""

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "build" / "out" / "dist" / "xat-rag-service"
SETUP = ROOT / "eval" / "fixtures" / "setup"


def package() -> None:
    """Build the frozen folder."""
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "xat-rag-service.spec",
            "--noconfirm",
            "--distpath",
            "out/dist",
            "--workpath",
            "out/work",
        ],
        cwd=ROOT / "build",
        check=True,
    )


# Top-level modules that must never be in the frozen folder (ADR 0011 section 4).
DENIED_MODULES = (
    "tiktoken",
    "openai",
    "posthog",
    "nltk",
    "torch",
    "transformers",
    "sentence_transformers",
    "sentry_sdk",
    "unstructured",
    "cryptography",
    "langchain_community",
    "langchain_classic",
    "langgraph",
    "sqlalchemy",
    "aiohttp",
    "PIL",
    "setuptools",
    "pkg_resources",
    "mistral_common",
)


def check_bundle() -> None:
    """Fail if the frozen folder holds a denied module, as a folder, a native library or a module in the archive."""
    internal = DIST / "_internal"
    names = {path.name.split(".")[0] for path in internal.iterdir()}
    found = sorted(name for name in DENIED_MODULES if name in names)
    archive = subprocess.run(  # noqa: S603 - fixed arguments.
        [
            sys.executable,
            "-m",
            "PyInstaller.utils.cliutils.archive_viewer",
            "--list",
            "--brief",
            str(DIST / ("xat-rag-service.exe" if os.name == "nt" else "xat-rag-service")),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    modules = {line.strip().split(".")[0] for line in archive.stdout.splitlines()}
    found += sorted(name for name in DENIED_MODULES if name in modules and name not in found)
    if found:
        sys.exit(f"The frozen folder holds denied modules: {', '.join(found)}")
    print("the frozen folder holds no denied module")


def _call(
    url: str,
    path: str,
    key: str,
    *,
    body: bytes | None = None,
    content_type: str = "application/json",
    method: str | None = None,
) -> tuple[int, Any]:
    headers = {"Authorization": f"Bearer {key}", "Content-Type": content_type}
    request = urllib.request.Request(url + path, data=body, method=method, headers=headers)  # noqa: S310 - loopback.
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - loopback.
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        body = error.read()
        try:
            return error.code, json.loads(body)
        except ValueError:
            return error.code, {"raw": body[:200].decode(errors="replace")}


def smoke_test() -> None:
    """Start the frozen service with the fixture setup, ingest one document, and make a context pack."""
    binary = DIST / ("xat-rag-service.exe" if os.name == "nt" else "xat-rag-service")
    key = secrets.token_hex(16)
    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        kept = {"PATH", "HOME", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "USERPROFILE", "APPDATA", "LOCALAPPDATA"}
        env = {k: v for k, v in os.environ.items() if k.upper() in kept}
        env |= {
            "XatRag__DataFolder": str(work / "data"),
            "XatRag__RecipesFolder": str(SETUP / "recipes"),
            "XatRag__PromptsFolder": str(SETUP / "prompts"),
            "XatRag__ModelsFolder": str(ROOT / "models"),
            "XatRag__LockFile": str(ROOT / "build" / "models.lock.json"),
            "XatRag__ReadyFile": str(work / "ready.json"),
            "XatRag__StopWhenInputCloses": "true",
            "XatRag__AccessKey": key,
            "LANGSMITH_TRACING": "true",
        }
        log = (work / "stderr.txt").open("wb")
        process = subprocess.Popen([str(binary)], env=env, stdin=subprocess.PIPE, stderr=log)  # noqa: S603
        try:
            started = time.monotonic()
            while not (work / "ready.json").exists():
                if process.poll() is not None or time.monotonic() - started > 300:
                    log.flush()
                    sys.exit(f"The service did not start: {(work / 'stderr.txt').read_text(errors='replace')[-2000:]}")
                time.sleep(0.1)
            print(f"ready in {time.monotonic() - started:.1f} s")
            url = json.loads((work / "ready.json").read_text())["url"]
            document = (SETUP / "documents" / "docs" / "orders.md").read_bytes()
            boundary = "smoke7b1"
            fields = json.dumps([{"doc_id": "docs:orders.md", "kind": "markdown", "part": "f"}]).encode()
            body = (
                f'--{boundary}\r\nContent-Disposition: form-data; name="documents"\r\n\r\n'.encode()
                + fields
                + f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="f"; filename="f"\r\n\r\n'.encode()
                + document
                + f"\r\n--{boundary}--\r\n".encode()
            )
            status, job = _call(
                url,
                "/v1/rags/docs/documents",
                key,
                body=body,
                content_type=f"multipart/form-data; boundary={boundary}",
                method="PUT",
            )
            assert status == 202, job
            for _ in range(300):
                status, state = _call(url, f"/v1/jobs/{job['job_id']}", key)
                if state["status"] in {"done", "failed"}:
                    break
                time.sleep(0.1)
            assert state["status"] == "done", state
            question = json.dumps({"context": "ask-docs", "question": "Which port do orders use?"}).encode()
            status, pack = _call(url, "/v1/context", key, body=question, method="POST")
            assert status == 200, pack
            assert pack["chunks"][0]["chunk_id"] == "docs:orders.md#h/orders-service/ports-and-health", pack["chunks"][
                0
            ]
            print("smoke test passed")
        finally:
            assert process.stdin is not None
            process.stdin.close()
            try:
                process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=30)
            log.close()


if __name__ == "__main__":
    {"package": package, "smoke-test": smoke_test, "check-bundle": check_bundle}[sys.argv[1]]()
