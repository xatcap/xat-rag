import socket
import subprocess
import sys
import textwrap

import pytest
from xat_rag_offline import BlockedOperationError, NetworkGuard, is_loopback_address


@pytest.mark.parametrize(
    "address",
    [
        ("127.0.0.1", 80),
        ("127.5.6.7", 80),
        ("::1", 80, 0, 0),
        ("::ffff:127.0.0.1", 80, 0, 0),
        ("localhost", 80),
        "/tmp/xat-rag.sock",
        b"\x00abstract",
    ],
)
def test_loopback_addresses_are_allowed(address: object) -> None:
    assert is_loopback_address(address)


@pytest.mark.parametrize(
    "address",
    [
        ("0.0.0.0", 80),
        ("::", 80, 0, 0),
        ("", 80),
        ("10.0.0.1", 80),
        ("example.com", 443),
        ("::ffff:8.8.8.8", 1, 0, 0),
        (),
    ],
)
def test_other_addresses_are_refused(address: object) -> None:
    assert not is_loopback_address(address)


def test_guard_refuses_remote_connect_and_dns() -> None:
    guard = NetworkGuard()
    sock = object()
    guard.check("socket.connect", (sock, ("127.0.0.1", 8080)))
    guard.check("socket.getaddrinfo", ("localhost", 80, 0, 0, 0))
    with pytest.raises(BlockedOperationError, match=r"socket\.connect"):
        guard.check("socket.connect", (sock, ("93.184.216.34", 443)))
    with pytest.raises(BlockedOperationError):
        guard.check("socket.sendto", (sock, ("8.8.8.8", 53)))
    with pytest.raises(BlockedOperationError):
        guard.check("socket.bind", (sock, ("0.0.0.0", 0)))
    with pytest.raises(BlockedOperationError):
        guard.check("socket.getaddrinfo", ("api.smith.langchain.com", 443, 0, 0, 0))
    with pytest.raises(BlockedOperationError):
        guard.check("socket.getaddrinfo", (None, 443, 0, 0, 0))
    with pytest.raises(BlockedOperationError):
        guard.check("socket.gethostbyname", ("example.com",))


def test_guard_refuses_processes() -> None:
    guard = NetworkGuard()
    with pytest.raises(BlockedOperationError):
        guard.check("subprocess.Popen", ("curl", ["curl"], None, None))
    with pytest.raises(BlockedOperationError):
        guard.check("os.system", (b"curl x",))


def test_guard_allows_only_listed_libraries_and_extensions(tmp_path: object) -> None:
    guard = NetworkGuard(allowed_libraries=["libc.dylib"], allowed_extensions=["/opt/x/vec0.dylib"])
    guard.check("ctypes.dlopen", ("libc.dylib",))
    guard.check("ctypes.dlopen", (None,))
    guard.check("sqlite3.load_extension", (object(), "/opt/x/vec0.dylib"))
    with pytest.raises(BlockedOperationError):
        guard.check("ctypes.dlopen", ("libevil.dylib",))
    with pytest.raises(BlockedOperationError):
        guard.check("sqlite3.load_extension", (object(), "/tmp/evil.dylib"))


def test_disabled_guard_allows_everything() -> None:
    guard = NetworkGuard()
    guard.enabled = False
    guard.check("socket.connect", (object(), ("8.8.8.8", 53)))


def test_on_block_gets_the_event_name() -> None:
    events: list[str] = []
    guard = NetworkGuard(on_block=events.append)
    with pytest.raises(BlockedOperationError):
        guard.check("socket.connect", (object(), ("8.8.8.8", 53)))
    assert events == ["socket.connect"]


def test_installed_guard_blocks_a_real_socket_in_a_fresh_process() -> None:
    # The audit hook cannot be removed, so this test runs in a child process.
    code = textwrap.dedent(
        """
        import socket, tempfile
        from pathlib import Path
        from xat_rag_offline import harden_process, BlockedOperationError
        harden_process(Path(tempfile.gettempdir()))
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.connect(("192.0.2.1", 9))
        except BlockedOperationError:
            print("blocked")
        """
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False)
    assert result.stdout.strip() == "blocked", result.stderr
    _ = socket  # The parent process is not changed.


def test_connected_sendmsg_without_address_is_allowed() -> None:
    guard = NetworkGuard()
    guard.check("socket.sendmsg", (object(), None))
    guard.check("socket.sendto", (object(), None))
    with pytest.raises(BlockedOperationError):
        guard.check("socket.connect", (object(), None))
    with pytest.raises(BlockedOperationError):
        guard.check("socket.connect", (object(),))


def test_ipv4_text_with_percent_is_not_loopback() -> None:
    assert not is_loopback_address(("127.0.0.1%secret.evil.example", 80))
    assert is_loopback_address(("::1%lo0", 80, 0, 0))


def test_reverse_dns_is_guarded() -> None:
    guard = NetworkGuard()
    guard.check("socket.getnameinfo", (("127.0.0.1", 80), 0))
    with pytest.raises(BlockedOperationError):
        guard.check("socket.getnameinfo", (("8.8.8.8", 53), 0))
    with pytest.raises(BlockedOperationError):
        guard.check("socket.gethostbyaddr", ("8.8.8.8",))


def test_large_asyncio_write_on_loopback_works_with_the_guard() -> None:
    code = textwrap.dedent(
        """
        import asyncio, tempfile
        from pathlib import Path
        from xat_rag_offline import harden_process
        harden_process(Path(tempfile.gettempdir()))
        SIZE = 8 * 1024 * 1024

        async def main():
            received = 0
            done = asyncio.Event()
            async def handle(reader, writer):
                writer.write(b"x" * SIZE)
                await writer.drain()
                writer.close()
            server = await asyncio.start_server(handle, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            while chunk := await reader.read(65536):
                received += len(chunk)
            writer.close()
            server.close()
            print(received)

        asyncio.run(main())
        """
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False)
    assert result.stdout.strip() == str(8 * 1024 * 1024), result.stderr
