"""Release tasks of xat-rag.

uv run python build/release.py package       # build the frozen service folder with PyInstaller
uv run python build/release.py smoke-test    # start the frozen service and check it end to end
uv run python build/release.py archive v1.2.3          # archive the frozen folder for this platform
uv run python build/release.py archive-models v1.2.3   # archive the locked models and the lock file
"""

import functools
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "build" / "out" / "dist" / "xat-rag-service"
RELEASE = ROOT / "build" / "out" / "release"
LOCK = ROOT / "build" / "models.lock.json"
SETUP = ROOT / "eval" / "fixtures" / "setup"
# GitHub does not accept a release file of this size or larger.
RELEASE_FILE_LIMIT = 2 * 1024**3


def package() -> None:
    """Build the frozen folder. Add the default recipes, and the empty `data` and `models` folders."""
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
    shutil.copytree(SETUP / "recipes", DIST / "recipes")
    (DIST / "data").mkdir()
    (DIST / "models").mkdir()


# Build tools and optional imports that must not be in the frozen folder.
EXCLUDED_MODULES = (
    "setuptools",
    "pkg_resources",
    "mistral_common",
)


def check_bundle() -> None:
    """Fail if the frozen folder holds an excluded module, as a folder, a native library or a module in the archive."""
    internal = DIST / "_internal"
    names = {path.name.split(".")[0] for path in internal.iterdir()}
    found = sorted(name for name in EXCLUDED_MODULES if name in names)
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
    found += sorted(name for name in EXCLUDED_MODULES if name in modules and name not in found)
    if found:
        sys.exit(f"The frozen folder holds excluded modules: {', '.join(found)}")
    print("the frozen folder holds no excluded module")
    if not any((internal / "sqlite_vec").glob("vec0.*")):
        sys.exit("The frozen folder holds no sqlite_vec/vec0 extension file.")
    print("the frozen folder holds the sqlite_vec extension")
    _check_default_folders()
    # The archive is smaller than the folder. Thus this check fails a pull request before a release can fail.
    size = sum(path.stat().st_size for path in DIST.rglob("*") if path.is_file() and not path.is_symlink())
    print(f"the frozen folder has {size / 1024**2:.0f} MiB")
    if size >= RELEASE_FILE_LIMIT:
        sys.exit(f"The frozen folder has {size} bytes. Its archive can be too large for a GitHub release.")


def _check_default_folders() -> None:
    """Fail unless `data` and `models` exist and are empty, and `recipes` holds the default recipes."""
    for name in ("data", "models"):
        folder = DIST / name
        if not folder.is_dir() or any(folder.iterdir()):
            sys.exit(f"The folder '{folder}' must exist and be empty.")
    if not any((DIST / "recipes" / "rags").glob("*.toml")):
        sys.exit(f"The folder '{DIST / 'recipes'}' holds no default recipes.")
    print("the frozen folder holds the default folders")


def _call(
    url: str,
    path: str,
    *,
    body: bytes | None = None,
    content_type: str = "application/json",
    method: str | None = None,
) -> tuple[int, Any]:
    headers = {"Content-Type": content_type}
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


def _binary(folder: Path) -> Path:
    return folder / ("xat-rag-service.exe" if os.name == "nt" else "xat-rag-service")


def _locked_model_names() -> list[str]:
    models = json.loads(LOCK.read_text())["models"]
    return [f"{model['id']}/{entry['path']}" for model in models for entry in model["files"]]


def _base_environment() -> dict[str, str]:
    kept = {"PATH", "HOME", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "USERPROFILE", "APPDATA", "LOCALAPPDATA"}
    env = {k: v for k, v in os.environ.items() if k.upper() in kept}
    return env | {"XatRag__StopWhenInputCloses": "true"}


def smoke_test() -> None:
    """Start the frozen service with explicit settings, and then with the default paths of a release copy."""
    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        explicit = work / "explicit"
        explicit.mkdir()
        env = _base_environment() | {
            "XatRag__DataFolder": str(explicit / "data"),
            "XatRag__RecipesFolder": str(DIST / "recipes"),
            "XatRag__ModelsFolder": str(ROOT / "models"),
            "XatRag__LockFile": str(LOCK),
            "XatRag__ReadyFile": str(explicit / "ready.json"),
        }
        _exercise(_binary(DIST), env, explicit / "ready.json", explicit, ROOT)

        # The defaults run on a copy, so that the archive of the frozen folder stays free of models and data. The
        # path has a space. Do not add a non-ASCII character, because the PDF parser of Docling fails on such a path
        # on Windows.
        copy = work / "xat rag" / DIST.name
        shutil.copytree(DIST, copy, symlinks=True)
        for name in _locked_model_names():
            target = copy / "models" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / "models" / name, target)
        shutil.copy2(LOCK, copy / "models" / LOCK.name)
        ready = copy / "data" / "ready.json"
        _exercise(_binary(copy), _base_environment(), ready, work, work)
        if ready.exists():
            sys.exit("The service did not delete its ready file when it stopped.")
    _check_default_folders()
    print("smoke test passed")


def _report_stuck(pid: int, logs: Path) -> None:
    """Print the end of the service log and, if py-spy is installed, the stack of each thread of the service.

    This function does not raise, so that the failed check of the job stays the error of the smoke test.
    """
    stdout = sys.stdout
    if isinstance(stdout, io.TextIOWrapper):  # A Windows console encoding cannot show all characters.
        cast("io.TextIOWrapper[io.BufferedWriter]", stdout).reconfigure(errors="replace")
    log = (logs / "stderr.txt").read_text(encoding="utf-8", errors="replace")[-4000:]
    print(f"The service log:\n{log}", flush=True)
    spy = shutil.which("py-spy")
    if spy is None:
        print("py-spy is not installed, so there is no thread dump.", flush=True)
        return
    # The native frames show a stop in native code, for example in the PDF parser. macOS does not support them.
    command: list[str] = [spy, "dump", "--pid", str(pid), *([] if sys.platform == "darwin" else ["--native"])]
    try:
        dump = subprocess.run(  # noqa: S603 - fixed arguments.
            command, capture_output=True, encoding="utf-8", errors="replace", timeout=120, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"py-spy did not make a thread dump: {type(error).__name__}", flush=True)
        return
    print(f"The threads of the service:\n{dump.stdout}{dump.stderr}", flush=True)


def _upload_and_wait(url: str, rag: str, doc_id: str, kind: str, path: Path, *, on_stuck: Callable[[], None]) -> None:
    """Upload one document and wait until its job ends well. If the job does not end well, report the service state."""
    boundary = "smoke7b1"
    fields = json.dumps([{"doc_id": doc_id, "kind": kind, "part": "f"}]).encode()
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="documents"\r\n\r\n'.encode()
        + fields
        + f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="f"; filename="f"\r\n\r\n'.encode()
        + path.read_bytes()
        + f"\r\n--{boundary}--\r\n".encode()
    )
    status, job = _call(
        url,
        f"/v1/rags/{rag}/documents",
        body=body,
        content_type=f"multipart/form-data; boundary={boundary}",
        method="PUT",
    )
    assert status == 202, job
    state: dict[str, Any] = {}
    for _ in range(3000):
        status, state = _call(url, f"/v1/jobs/{job['job_id']}")
        if state["status"] in {"done", "failed"}:
            break
        time.sleep(0.1)
    if state["status"] != "done" or state["results"][0]["status"] != "created":
        on_stuck()
    assert state["status"] == "done", state
    assert state["results"][0]["status"] == "created", state


def _exercise(binary: Path, env: dict[str, str], ready: Path, logs: Path, cwd: Path) -> None:
    """Start the service, ingest one document, make a retrieval result, and stop the service."""
    log = (logs / "stderr.txt").open("wb")
    process = subprocess.Popen([str(binary)], env=env, stdin=subprocess.PIPE, stderr=log, cwd=cwd)  # noqa: S603
    try:
        started = time.monotonic()
        while not ready.exists():
            if process.poll() is not None or time.monotonic() - started > 300:
                log.flush()
                sys.exit(f"The service did not start: {(logs / 'stderr.txt').read_text(errors='replace')[-2000:]}")
            time.sleep(0.1)
        print(f"ready in {time.monotonic() - started:.1f} s")
        url = json.loads(ready.read_text())["url"]
        documents = SETUP / "documents"
        report = functools.partial(_report_stuck, process.pid, logs)
        _upload_and_wait(url, "docs", "docs:orders.md", "markdown", documents / "docs" / "orders.md", on_stuck=report)
        # A PDF file exercises the layout models and the native libraries of the frozen folder.
        _upload_and_wait(url, "pdfs", "pdfs:carriers.pdf", "pdf", documents / "pdfs" / "carriers.pdf", on_stuck=report)
        question = json.dumps({"context": "ask-docs", "question": "Which port do orders use?"}).encode()
        status, result = _call(url, "/v1/context", body=question, method="POST")
        assert status == 200, result
        assert result["chunks"], result
        first = result["chunks"][0]
        assert first["chunk_id"] == "docs:orders.md#h/orders-service/ports-and-health", first
        assert first["source"]["doc_id"] == "docs:orders.md", first
        assert first["source"]["span"]["type"] == "line", first
    finally:
        assert process.stdin is not None
        process.stdin.close()
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=30)
        log.close()


def archive(version: str) -> None:
    """Archive the frozen folder: a zip file on Windows, and a tar file on other systems, to keep file modes."""
    _check_default_folders()
    system = {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}[platform.system()]
    machine = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}[platform.machine().lower()]
    RELEASE.mkdir(parents=True, exist_ok=True)
    path = shutil.make_archive(
        str(RELEASE / f"xat-rag-service-{version}-{system}-{machine}"),
        "zip" if os.name == "nt" else "gztar",
        root_dir=DIST.parent,
        base_dir=DIST.name,
    )
    _check_size(Path(path))
    print(path)


def _check_size(path: Path) -> None:
    """Fail if the file is too large for a GitHub release, so that the tag build stops before the publish job."""
    size = path.stat().st_size
    print(f"{path.name}: {size / 1024**2:.0f} MiB")
    if size >= RELEASE_FILE_LIMIT:
        sys.exit(f"The file '{path}' has {size} bytes. A GitHub release file must have less than {RELEASE_FILE_LIMIT}.")


def archive_models(version: str) -> None:
    """Archive the files that the lock lists, with the lock file. The contents match `XatRag__ModelsFolder`."""
    RELEASE.mkdir(parents=True, exist_ok=True)
    path = RELEASE / f"xat-rag-models-{version}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as handle:
        handle.write(LOCK, LOCK.name)
        for name in _locked_model_names():
            handle.write(ROOT / "models" / name, name)
    _check_size(path)
    print(path)


if __name__ == "__main__":
    tasks = {
        "package": package,
        "smoke-test": smoke_test,
        "check-bundle": check_bundle,
        "archive": lambda: archive(sys.argv[2]),
        "archive-models": lambda: archive_models(sys.argv[2]),
    }
    tasks[sys.argv[1]]()
