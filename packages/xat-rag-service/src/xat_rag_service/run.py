"""Start the service after `harden_process()`: settings, logs, lock, models, app, server and lifecycle.

The lifecycle follows the engine: port 0, a ready file, a stop when standard input closes, and one
service for each data folder. See ADR 0003.
"""

import contextlib
import json
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import IO, Any

import langsmith
import uvicorn
from langchain_core.globals import set_debug, set_verbose
from xat_rag import Settings, XatRag
from xat_rag_core.errors import XatRagError
from xat_rag_offline import Hardening

from xat_rag_service import VERSION
from xat_rag_service.app import create_app
from xat_rag_service.settings import ServiceSettings, read_settings

LOG = logging.getLogger("xat_rag")


def configure_logs() -> None:
    """Log errors and events only. Logs never hold content (ADR 0010).

    pypdf and other libraries can log parts of a bad file, so their logs are off.
    """
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for name in ("pypdf", "docx", "bs4", "uvicorn.access", "httpx", "langsmith", "onnxruntime"):
        logging.getLogger(name).setLevel(logging.CRITICAL + 1)


def lock_data_folder(folder: Path) -> IO[bytes]:
    """Take the lock of the data folder, so that only one service uses it. Raise `SystemExit` if it is taken."""
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        folder.chmod(0o700)  # The indexes hold sensitive text (architecture, section 14).
    handle = (folder / "xat-rag.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt  # noqa: PLC0415 - Windows only.

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
        else:
            import fcntl  # noqa: PLC0415 - POSIX only.

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        handle.close()
        raise SystemExit(f"xat-rag: another service uses the data folder '{folder}'. Stop it first.") from error
    return handle


def write_ready_file(path: Path, url: str) -> None:
    """Write `{url, pid, version}` atomically."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"url": url, "pid": os.getpid(), "version": VERSION}))
    os.replace(temporary, path)


def _watch_ready(server: uvicorn.Server, settings: ServiceSettings) -> None:
    while not server.started and not server.should_exit:
        time.sleep(0.02)
    if server.should_exit or settings.ready_file is None:
        return
    if settings.listen.unix_path is not None:
        # uvicorn makes the socket readable and writable for all users. Only this user may connect.
        Path(settings.listen.unix_path).chmod(0o600)
        url = f"http://unix:{settings.listen.unix_path}"
    else:
        sockets: list[Any] = [s for srv in server.servers for s in srv.sockets]
        host, port = sockets[0].getsockname()[:2]
        url = f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"
    write_ready_file(settings.ready_file, url)


def _watch_input(server: uvicorn.Server) -> None:
    stdin = sys.stdin
    try:
        while stdin is not None and stdin.buffer.read(4096):
            pass
    except (OSError, ValueError):
        pass
    server.should_exit = True


class _Server(uvicorn.Server):
    """A uvicorn server that stops on SIGINT and SIGTERM without raising the signal again.

    uvicorn raises the signal again after it stops, which ends the process before the cleanup of
    `run` (jobs, stores, ready file). Here the signal only asks the server to stop.
    """

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None]:
        previous = {sig: signal.signal(sig, self.handle_exit) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            yield
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def run(hardening: Hardening) -> int:
    """Run the service until it stops. Return the exit code."""
    configure_logs()
    langsmith.configure(enabled=False)  # pyright: ignore[reportUnknownMemberType]
    set_debug(False)
    set_verbose(False)
    try:
        settings = read_settings(os.environ)
        if not hardening.access_key or len(hardening.access_key) < 16 or not hardening.access_key.isascii():
            raise SystemExit("xat-rag: set XatRag__AccessKey to a random ASCII key of at least 16 characters.")
        if settings.ready_file is not None and settings.ready_file.exists():
            settings.ready_file.unlink()  # A stale file from a killed service must not point to an old address.
        lock = lock_data_folder(settings.data_folder)
        rag = XatRag.open(
            Settings(
                data_folder=settings.data_folder,
                recipes_folder=settings.recipes_folder,
                models_folder=settings.models_folder,
                lock_file=settings.lock_file,
                prompts_folder=settings.prompts_folder,
                threads=settings.threads,
            )
        )
        app = create_app(rag, hardening.access_key, settings.queue_limit, VERSION)
    except XatRagError as error:
        print(f"xat-rag: {error.message}", file=sys.stderr)
        return 2
    listen = settings.listen
    config = uvicorn.Config(
        app,
        host=listen.host or "127.0.0.1",
        port=listen.port or 0,
        uds=listen.unix_path,
        log_level="warning",
        access_log=False,
        server_header=False,
        date_header=False,
        lifespan="off",
    )
    server = _Server(config)
    threading.Thread(target=_watch_ready, args=(server, settings), daemon=True).start()
    if settings.stop_when_input_closes:
        threading.Thread(target=_watch_input, args=(server,), daemon=True).start()
    try:
        server.run()
    finally:
        app.state.jobs.shutdown(timeout_seconds=30)
        app.state.limiter.shutdown()
        rag.close()
        if settings.ready_file is not None and settings.ready_file.exists():
            settings.ready_file.unlink()
        lock.close()
    return 0
