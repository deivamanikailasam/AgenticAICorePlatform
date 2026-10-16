"""Repository-wide pytest fixtures."""

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

_EXPORTER = InMemorySpanExporter()


@pytest.fixture
def span_exporter() -> InMemorySpanExporter:
    """Spans recorded by the global tracer provider. OTel allows setting that provider only once
    per process, so every test shares this exporter; it is cleared before each test."""
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
        trace.set_tracer_provider(provider)
    _EXPORTER.clear()
    return _EXPORTER


# ---------------------------------------------------------------- no network (Step 33)

_LIVE_MARKERS = ("integration", "mac", "sandbox", "microvm")
# hosts a test may reach: this machine (0.0.0.0 as a connect target means this host), and the
# names httpx gives in-process ASGI transports
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "", "t", "testserver"}  # noqa: S104


def _is_local(host: object) -> bool:
    import ipaddress

    if not isinstance(host, str) or host in _LOCAL_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests may reach only this machine (loopback, Unix sockets): `just test` runs with
    no network. Tests marked integration, mac, sandbox or microvm talk to real services."""
    if any(request.node.get_closest_marker(m) for m in _LIVE_MARKERS):
        return
    import socket

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def guard(address: object) -> None:
        host = address[0] if isinstance(address, tuple) else None
        if host is not None and not _is_local(host):
            raise OSError(f"unit tests have no network: tried to connect to {address!r}")

    def connect(sock: socket.socket, address: object) -> None:
        guard(address)
        return real_connect(sock, address)  # type: ignore[arg-type]

    def connect_ex(sock: socket.socket, address: object) -> int:
        guard(address)
        return real_connect_ex(sock, address)  # type: ignore[arg-type]

    def getaddrinfo(host: object, *args: object, **kwargs: object) -> object:
        if not _is_local(host if not isinstance(host, bytes) else host.decode()):
            raise OSError(f"unit tests have no network: tried to resolve {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)  # type: ignore[arg-type]

    # a proxy on loopback (as in some build containers) would be a way out: unit tests get none
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
