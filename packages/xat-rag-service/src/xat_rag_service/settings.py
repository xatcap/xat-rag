"""The service settings, from `XatRag__*` environment variables. Names are compared without case."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from xat_rag_core.errors import ConfigError


@dataclass(frozen=True, slots=True)
class Listen:
    """Where the service listens: a loopback host and port, or a Unix socket path."""

    host: str | None
    port: int | None
    unix_path: str | None


@dataclass(frozen=True, slots=True)
class ServiceSettings:
    """The settings of the service."""

    listen: Listen
    data_folder: Path
    recipes_folder: Path
    models_folder: Path
    lock_file: Path
    ready_file: Path | None
    stop_when_input_closes: bool
    queue_limit: int
    threads: int


def _get(environ: Mapping[str, str], name: str) -> str | None:
    wanted = f"XATRAG__{name}".upper()
    for key, value in environ.items():
        if key.upper() == wanted:
            return value
    return None


def _required(environ: Mapping[str, str], name: str) -> Path:
    value = _get(environ, name)
    if not value:
        raise ConfigError(f"Set XatRag__{name}.")
    return Path(value)


def parse_listen(url: str) -> Listen:
    """Read `XatRag__Urls`. Only loopback addresses and Unix sockets are accepted."""
    if url.startswith("http://unix:"):
        path = url.removeprefix("http://unix:")
        if not path:
            raise ConfigError("XatRag__Urls needs a socket path after http://unix:.")
        return Listen(None, None, path)
    message = "XatRag__Urls must be http://127.0.0.1:<port>, http://[::1]:<port> or http://unix:<path>."
    try:
        parts = urlsplit(url)
        port = parts.port or 0
    except ValueError as error:
        raise ConfigError(message) from error
    if parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ConfigError(message)
    host = "127.0.0.1" if parts.hostname == "localhost" else parts.hostname
    return Listen(host, port, None)


def read_settings(environ: Mapping[str, str]) -> ServiceSettings:
    """Read the settings. Raise `ConfigError` with an actionable message on a problem."""
    ready = _get(environ, "ReadyFile")
    try:
        queue_limit = int(_get(environ, "QueueLimit") or "8")
        threads = int(_get(environ, "Threads") or "0")
    except ValueError as error:
        raise ConfigError("XatRag__QueueLimit and XatRag__Threads must be whole numbers.") from error
    if not 1 <= queue_limit <= 1000 or not 0 <= threads <= 256:
        raise ConfigError("XatRag__QueueLimit must be 1 to 1000, and XatRag__Threads 0 to 256.")
    return ServiceSettings(
        listen=parse_listen(_get(environ, "Urls") or "http://127.0.0.1:0"),
        data_folder=_required(environ, "DataFolder"),
        recipes_folder=_required(environ, "RecipesFolder"),
        models_folder=_required(environ, "ModelsFolder"),
        lock_file=_required(environ, "LockFile"),
        ready_file=Path(ready) if ready else None,
        stop_when_input_closes=(_get(environ, "StopWhenInputCloses") or "").lower() == "true",
        queue_limit=queue_limit,
        threads=threads,
    )
