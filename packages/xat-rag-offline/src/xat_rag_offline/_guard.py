"""A network guard that uses a Python audit hook.

The guard is defense in depth only. It sees only Python audit events, so native code
(for example ONNX Runtime or a SQLite extension) can pass it.
"""

import ipaddress
import sys
import threading
from collections.abc import Callable, Collection, Sequence
from pathlib import Path
from typing import cast

_SOCKET_ADDRESS_EVENTS = frozenset({"socket.connect", "socket.sendto", "socket.sendmsg", "socket.bind"})
_PROCESS_EVENTS = frozenset(
    {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty"}
)


class BlockedOperationError(RuntimeError):
    """The network guard refused an operation. The message names the audit event only."""


def is_loopback_address(address: object) -> bool:
    """Return `True` if `address` is a loopback address or a Unix socket path.

    Loopback means `127.0.0.0/8`, `::1`, an IPv4-mapped loopback address such as
    `::ffff:127.0.0.1`, and the name `localhost`. Wildcard addresses (`0.0.0.0`, `::`, `""`)
    are not loopback.
    """
    if isinstance(address, (str, bytes, bytearray)):
        return True  # An AF_UNIX path. File permissions control access.
    if not isinstance(address, tuple) or not address:
        return False
    parts = cast(tuple[object, ...], address)
    return is_loopback_host(parts[0])


def is_loopback_host(host: object) -> bool:
    """Return `True` if `host` is a loopback host name or IP address."""
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="replace")
    if not isinstance(host, str) or not host:
        return False
    if host.lower() == "localhost":
        return True
    if ":" in host:
        host = host.split("%", 1)[0]  # An IPv6 zone ID. IPv4 text with "%" is a host name, not an address.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped.is_loopback
    return ip.is_loopback


class NetworkGuard:
    """An audit hook that refuses process events, unlisted SQLite extensions and network events that are not loopback.

    An audit hook cannot be removed. Use `enabled` to turn the checks off, for example in tests.
    """

    def __init__(
        self,
        allowed_extensions: Collection[str] = (),
        on_block: Callable[[str], None] | None = None,
    ) -> None:
        """Make a guard.

        `allowed_extensions` lists the SQLite extension paths that can load. `on_block` gets
        the event name when the guard refuses an event.
        """
        self._allowed_extensions = frozenset(_normalize(path) for path in allowed_extensions)
        self._on_block = on_block
        self._lock = threading.Lock()
        self._installed = False
        self.enabled = True

    def install(self) -> None:
        """Install the audit hook. A second call does nothing."""
        with self._lock:
            if self._installed:
                return
            sys.addaudithook(self._hook)
            self._installed = True

    def check(self, event: str, args: Sequence[object]) -> None:
        """Raise `BlockedOperationError` if the guard refuses `event` with `args`."""
        if not self.enabled:
            return
        if event in _SOCKET_ADDRESS_EVENTS:
            self._check_socket_address(event, args)
        elif event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}:
            if not args or not is_loopback_host(args[0]):
                self._block(event)
        elif event == "socket.getnameinfo":
            if not args or not is_loopback_address(args[0]):
                self._block(event)
        elif event in _PROCESS_EVENTS:
            self._block(event)
        elif event == "sqlite3.load_extension":
            path = args[1] if len(args) >= 2 else None
            if path is None or _normalize(path) not in self._allowed_extensions:
                self._block(event)

    def _check_socket_address(self, event: str, args: Sequence[object]) -> None:
        if len(args) < 2:
            self._block(event)
        address = args[1]
        # `sendmsg` and `sendto` on a connected socket send no address. The connect was checked.
        if address is None and event in {"socket.sendmsg", "socket.sendto"}:
            return
        if not is_loopback_address(address):
            self._block(event)

    def _hook(self, event: str, args: tuple[object, ...]) -> None:
        self.check(event, args)

    def _block(self, event: str) -> None:
        if self._on_block is not None:
            self._on_block(event)
        raise BlockedOperationError(f"The network guard refused the operation '{event}'.")


def _normalize(name: object) -> str:
    if isinstance(name, bytes):
        name = name.decode("utf-8", errors="replace")
    text = str(name)
    try:
        return str(Path(text).resolve()) if ("/" in text or "\\" in text) else text
    except OSError:
        return text
