"""Run the gateway: ``a2a-gateway`` (or ``python -m a2a_gateway``)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import uvicorn
from agent_contracts.dummies.observability import JsonlAuditSink
from agent_spec import load_spec
from agent_spec.platform import load_platform
from agent_tools import pdp_factory
from agent_tools.a2a import card_signer
from agent_tools.auth import KeycloakTokenVerifier
from agent_tools.secrets import default_resolver, maybe_secret

from a2a_gateway.app import A2aGateway
from a2a_gateway.inbound import Inbound
from a2a_gateway.settings import Settings


def build(settings: Settings) -> A2aGateway:
    platform = load_platform()
    publish = platform.a2a.publish
    key = asyncio.run(maybe_secret(publish.signing_key, default_resolver()))
    if not key:
        raise SystemExit("a2a.publish.signing_key did not resolve: the gateway signs every card")
    specs = {
        spec.name: spec
        for spec in (load_spec(p) for p in sorted(Path(settings.agents_dir).glob("*/agent.yaml")))
    }
    published = sorted(n for n, s in specs.items() if s.interop.a2a_publish)
    try:
        pdp = pdp_factory.build_pdp(
            settings.pdp, opa_url=settings.opa_url, cerbos_url=settings.cerbos_url
        )
    except ValueError as exc:
        raise SystemExit(f"A2A_GATEWAY_PDP: {exc}") from None
    verifier = KeycloakTokenVerifier(
        settings.issuer,
        audience=settings.audience,
        jwks_url=f"{settings.internal_issuer}/protocol/openid-connect/certs",
    )
    # Audit goes to a local hash-chained JSONL file until Step 31 adds the Project 0 adapter.
    inbound = Inbound(verifier, pdp, JsonlAuditSink(settings.audit_file), published)
    return A2aGateway(
        specs,
        publish,
        inbound,
        card_signer(key, publish.key_id),
        httpx.AsyncClient(base_url=settings.api_url, timeout=30),
        settings.issuer,
        grpc_agent=settings.grpc_agent,
        grpc_public=settings.grpc_public,
    )


async def serve(settings: Settings) -> None:
    gateway = build(settings)
    server = uvicorn.Server(
        uvicorn.Config(gateway.app(), host=settings.host, port=settings.port, log_level="info")
    )
    if not settings.grpc_agent:
        await server.serve()
        return
    grpc = gateway.grpc_server(settings.grpc_agent, settings.grpc_port, settings.host)
    await grpc.start()
    try:
        await server.serve()
    finally:
        await grpc.stop(5)


def main() -> None:
    asyncio.run(serve(Settings()))


if __name__ == "__main__":
    main()
