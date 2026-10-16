"""Step 33: `just test` runs with no network. The root conftest refuses every connection and name
lookup that leaves the machine; loopback (the in-process stand-ins and fakes) still works."""

import socket

import httpx
import pytest


def test_connecting_off_the_machine_is_refused() -> None:
    with socket.socket() as s, pytest.raises(OSError, match="unit tests have no network"):
        s.connect(("203.0.113.10", 443))  # TEST-NET-3: never routable


def test_name_lookups_are_refused() -> None:
    with pytest.raises(OSError, match="unit tests have no network"):
        socket.getaddrinfo("example.com", 443)


async def test_http_clients_are_refused_too() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.ConnectError, match="no network"):
            await client.get("https://example.com/")


def test_loopback_still_works() -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.socket() as client:
            client.connect(server.getsockname())


@pytest.mark.integration
def test_integration_tests_are_not_guarded() -> None:
    assert socket.socket.connect.__qualname__ == "socket.connect"
