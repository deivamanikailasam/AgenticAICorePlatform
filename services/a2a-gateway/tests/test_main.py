"""The gateway starts inside the event loop that serves it (Step 38): the card-signing key is
resolved there, not with a nested asyncio.run (which crashed the pod on Kubernetes)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from a2a_gateway import __main__ as gateway_main
from a2a_gateway.settings import Settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

ROOT = Path(__file__).resolve().parents[3]


class _Resolver:
    def __init__(self, value: str) -> None:
        self.value, self.asked = value, []

    async def resolve(self, reference: str) -> str:
        self.asked.append(reference)
        return self.value


def test_build_resolves_the_signing_key_inside_the_running_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pem = (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )
    resolver = _Resolver(pem)
    monkeypatch.setattr(gateway_main, "default_resolver", lambda: resolver)
    settings = Settings(agents_dir=str(ROOT / "agents"), audit_file=str(tmp_path / "audit.jsonl"))

    async def start() -> object:
        return gateway_main.build(settings, await gateway_main.signing_key())  # as serve() does

    gateway = asyncio.run(start())
    assert resolver.asked == ["${secret:agent-platform/a2a#signing_key}"]
    assert gateway is not None
