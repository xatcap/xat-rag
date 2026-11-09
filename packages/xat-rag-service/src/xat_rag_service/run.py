"""Start the service after `harden_process()`: settings, logs, lock, models, app, server and lifecycle.

The lifecycle is: port 0, a ready file, a stop when standard input closes, and one
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

import uvicorn
from xat_rag import Settings, XatRag
from xat_rag_core.errors import ConfigError, XatRagError

from xat_rag_service import VERSION
from xat_rag_service.app import create_app
from xat_rag_service.paths import release_folder
from xat_rag_service.settings import ServiceSettings, read_settings

LOG = logging.getLogger("xat_rag")


def configure_logs() -> None:
    """Log errors and events only. Logs never hold content (ADR 0010).

    pypdf and other libraries can log parts of a bad file, so their logs are off.
    """
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for name in ("docling", "docling_parse", "uvicorn.access", "httpx", "onnxruntime"):
        logging.getLogger(name).setLevel(logging.CRITICAL + 1)


def lock_data_folder(folder: Path) -> IO[bytes]:
    """Take the lock of the data folder, so that only one service uses it. Raise `ConfigError` on a failure."""
    try:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            folder.chmod(0o700)  # The indexes hold sensitive text (architecture, section 14).
        handle = (folder / "xat-rag.lock").open("a+b")
    except OSError as error:
        raise ConfigError(
            f"The service cannot write to the data folder '{folder}'. "
            "Give this user write access, or set XatRag__DataFolder to another folder."
        ) from error
    try:
        if os.name == "nt":
            import msvcrt  # noqa: PLC0415 - Windows only.

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
        else:
            import fcntl  # noqa: PLC0415 - POSIX only.

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        handle.close()
        raise ConfigError(f"Another service uses the data folder '{folder}'. Stop it first.") from error
    return handle


def remove_ready_file(path: Path) -> None:
    """Delete a stale ready file. Call this only with the data folder lock, so that a running service keeps its file."""
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        raise ConfigError(
            f"The service cannot delete the ready file '{path}'. "
            "Delete the file, or set XatRag__ReadyFile to another path."
        ) from error


def write_ready_file(path: Path, url: str) -> None:
    """Write `{url, pid, version}` atomically."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"url": url, "pid": os.getpid(), "version": VERSION}))
        os.replace(temporary, path)
    except OSError:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise


class _Server(uvicorn.Server):
    """A uvicorn server that stops on SIGINT and SIGTERM without raising the signal again.

    uvicorn raises the signal again after it stops, which ends the process before the cleanup of
    `run` (jobs, stores, ready file). Here the signal only asks the server to stop.
    """

    def __init__(self, config: uvicorn.Config) -> None:
        super().__init__(config)
        self.start_failure: str | None = None

    def fail_start(self, message: str) -> None:
        """Keep the message of a failure after the server started, and ask the server to stop."""
        self.start_failure = message
        self.should_exit = True

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None]:
        previous = {sig: signal.signal(sig, self.handle_exit) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            yield
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def _watch_ready(server: _Server, settings: ServiceSettings) -> None:
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
    try:
        write_ready_file(settings.ready_file, url)
    except OSError:
        server.fail_start(
            f"The service cannot write the ready file '{settings.ready_file}'. "
            "Check the folder, or set XatRag__ReadyFile to another path."
        )


def _watch_input(server: uvicorn.Server) -> None:
    stdin = sys.stdin
    try:
        while stdin is not None and stdin.buffer.read(4096):
            pass
    except (OSError, ValueError):
        pass
    server.should_exit = True


def run() -> int:
    """Run the service until it stops. Return the exit code."""
    configure_logs()
    try:
        settings = read_settings(os.environ, release_folder())
        lock = lock_data_folder(settings.data_folder)
        if settings.ready_file is not None:
            remove_ready_file(settings.ready_file)  # A stale file from a killed service must not show an old address.
        if not settings.recipes_folder.is_dir():
            raise ConfigError(
                f"The recipes folder '{settings.recipes_folder}' does not exist. "
                "Extract the release again, or set XatRag__RecipesFolder."
            )
        rag = XatRag.open(
            Settings(
                data_folder=settings.data_folder,
                recipes_folder=settings.recipes_folder,
                models_folder=settings.models_folder,
                lock_file=settings.lock_file,
                threads=settings.threads,
            )
        )
        app = create_app(rag, settings.queue_limit, VERSION)
    except XatRagError as error:
        print(f"xat-rag: {error.message}", file=sys.stderr)
        return 2
    except OSError as error:
        target = f" for the file '{error.filename}'" if error.filename else ""
        print(
            f"xat-rag: The start failed{target}: {error.strerror}. Check the paths and their access.", file=sys.stderr
        )
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
        if settings.ready_file is not None:
            with contextlib.suppress(OSError):  # A failed delete must not stop the cleanup.
                settings.ready_file.unlink(missing_ok=True)
        lock.close()
    if server.start_failure is not None:
        print(f"xat-rag: {server.start_failure}", file=sys.stderr)
        return 2
    return 0
