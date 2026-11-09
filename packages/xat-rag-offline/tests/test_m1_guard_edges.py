import pytest
from xat_rag_offline import BlockedOperationError, NetworkGuard, is_loopback_address
from xat_rag_offline._guard import is_loopback_host  # pyright: ignore[reportPrivateUsage]

SOCK = object()


@pytest.mark.parametrize(
    "event",
    ["subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork"],
)
def test_every_process_event_is_refused(event: str) -> None:
    with pytest.raises(BlockedOperationError, match="network guard refused"):
        NetworkGuard().check(event, ())


@pytest.mark.parametrize(
    "event", ["open", "import", "exec", "socket.__new__", "unknown.event", "sqlite3.connect", "ctypes.dlopen"]
)
def test_other_events_pass(event: str) -> None:
    NetworkGuard().check(event, (object(),))


def test_sqlite_extension_needs_a_listed_path() -> None:
    guard = NetworkGuard(allowed_extensions=["/opt/x/vec0.so"])
    guard.check("sqlite3.load_extension", (object(), "/opt/x/vec0.so"))
    for args in [(), (object(),), (object(), None), (object(), "/opt/x/other.so")]:
        with pytest.raises(BlockedOperationError):
            guard.check("sqlite3.load_extension", args)


@pytest.mark.parametrize("event", ["socket.connect", "socket.sendto", "socket.sendmsg", "socket.bind"])
def test_address_events_allow_loopback_and_refuse_others(event: str) -> None:
    guard = NetworkGuard()
    guard.check(event, (SOCK, ("127.0.0.1", 1)))
    guard.check(event, (SOCK, ("::1", 1, 0, 0)))
    guard.check(event, (SOCK, "/run/x.sock"))
    for address in [("8.8.8.8", 1), ("0.0.0.0", 1), ("::", 1, 0, 0), ("", 1), 5]:
        with pytest.raises(BlockedOperationError):
            guard.check(event, (SOCK, address))
    if event in {"socket.sendto", "socket.sendmsg"}:
        guard.check(event, (SOCK, None))  # A connected socket sends no address. The connect was checked.
    else:
        with pytest.raises(BlockedOperationError):
            guard.check(event, (SOCK, None))


def test_address_event_without_an_address_argument_is_refused() -> None:
    with pytest.raises(BlockedOperationError):
        NetworkGuard().check("socket.connect", (SOCK,))


@pytest.mark.parametrize("event", ["socket.getaddrinfo", "socket.gethostbyname"])
def test_dns_events_allow_only_loopback_names(event: str) -> None:
    guard = NetworkGuard()
    for host in ["localhost", "LOCALHOST", "127.0.0.1", "::1", b"localhost"]:
        guard.check(event, (host, 80))
    for host in ["example.com", "", None, "localhost.example.com", "127.0.0.1.example.com", "0.0.0.0", 7]:
        with pytest.raises(BlockedOperationError):
            guard.check(event, (host, 80))
    with pytest.raises(BlockedOperationError):
        guard.check(event, ())


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("127.255.255.255", True),
        ("128.0.0.1", False),
        ("::1", True),
        ("::1%lo0", True),
        ("::ffff:127.0.0.1", True),
        ("::ffff:10.0.0.1", False),
        ("::ffff:0.0.0.0", False),
        ("0:0:0:0:0:0:0:1", True),
        ("fe80::1%en0", False),
        ("localhost", True),
        ("Localhost", True),
        ("localhost.", False),
        (b"127.0.0.1", True),
        (b"\xff\xfe", False),
        ("", False),
        (None, False),
        (127, False),
    ],
)
def test_is_loopback_host(host: object, expected: bool) -> None:
    assert is_loopback_host(host) is expected


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        (None, False),
        (5, False),
        ([], False),
        (["127.0.0.1", 80], False),
        (("127.0.0.1",), True),
        ((b"127.0.0.1", 1), True),
    ],
)
def test_is_loopback_address_for_odd_values(address: object, expected: bool) -> None:
    assert is_loopback_address(address) is expected


def test_on_block_runs_before_the_error_for_each_refused_event() -> None:
    events: list[str] = []
    guard = NetworkGuard(on_block=events.append)
    for event in ("os.system", "subprocess.Popen"):
        with pytest.raises(BlockedOperationError):
            guard.check(event, ())
    guard.check("socket.connect", (SOCK, ("127.0.0.1", 1)))
    assert events == ["os.system", "subprocess.Popen"]


def test_error_message_names_the_event_and_not_the_arguments() -> None:
    with pytest.raises(BlockedOperationError) as info:
        NetworkGuard().check("socket.connect", (SOCK, ("203.0.113.9", 4444)))
    assert "203.0.113.9" not in str(info.value)
    assert "socket.connect" in str(info.value)


def test_guard_can_be_turned_on_again() -> None:
    guard = NetworkGuard()
    guard.enabled = False
    guard.check("os.system", ())
    guard.enabled = True
    with pytest.raises(BlockedOperationError):
        guard.check("os.system", ())
