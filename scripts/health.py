"""Reach every Project 0 service that Project A depends on and report the result.

Run with ``just health``. Exit code 0 means every check passed.

Endpoints come from environment variables (see ``.env.example``) so the same script works on
the Mac (127.0.0.1), inside a container on Project 0's network (service names), and in CI
against the stand-in stack.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

import httpx

CheckFn = Callable[[], Awaitable[str]]


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    millis: int


@dataclass(frozen=True)
class Endpoints:
    litellm_url: str
    postgres_dsn: str
    valkey_url: str
    keycloak_url: str
    keycloak_realm: str
    opa_url: str
    otel_health_url: str
    openbao_url: str = "http://127.0.0.1:8200"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Endpoints:
        e = os.environ if env is None else env
        return cls(
            litellm_url=e.get("P0_LITELLM_URL", "http://127.0.0.1:4000"),
            postgres_dsn=e.get(
                "P0_POSTGRES_DSN", "postgresql://agent_health@127.0.0.1:5432/postgres"
            ),
            valkey_url=e.get("P0_VALKEY_URL", "redis://127.0.0.1:6379/0"),
            keycloak_url=e.get("P0_KEYCLOAK_URL", "http://127.0.0.1:8080"),
            keycloak_realm=e.get("P0_KEYCLOAK_REALM", "rag-platform"),
            opa_url=e.get("P0_OPA_URL", "http://127.0.0.1:8181"),
            otel_health_url=e.get("P0_OTEL_HEALTH_URL", "http://127.0.0.1:13133/"),
            openbao_url=e.get("P0_OPENBAO_URL", "http://127.0.0.1:8200"),
        )


def http_check(
    client: httpx.AsyncClient, url: str, expect: Callable[[httpx.Response], str]
) -> CheckFn:
    async def run() -> str:
        resp = await client.get(url)
        resp.raise_for_status()
        return expect(resp)

    return run


def _status(resp: httpx.Response) -> str:
    return f"HTTP {resp.status_code}"


def _oidc(resp: httpx.Response) -> str:
    issuer = resp.json().get("issuer")
    if not issuer:
        raise ValueError("no issuer in OIDC discovery document")
    return f"issuer {issuer}"


def _openbao(resp: httpx.Response) -> str:
    doc = resp.json()
    if doc.get("sealed") or not doc.get("initialized"):
        raise ValueError("OpenBao is sealed or not initialised")
    return f"unsealed, version {doc.get('version', '?')}"


def postgres_check(dsn: str) -> CheckFn:
    async def run() -> str:
        import psycopg

        async with await psycopg.AsyncConnection.connect(dsn, connect_timeout=5) as conn:
            cur = await conn.execute("select current_setting('server_version')")
            row = await cur.fetchone()
            return f"PostgreSQL {row[0] if row else '?'}"

    return run


def valkey_check(url: str) -> CheckFn:
    async def run() -> str:
        import redis.asyncio as redis

        client = redis.from_url(url, socket_timeout=5)
        try:
            pong = await client.ping()
            info = await client.info("server")
        finally:
            await client.aclose()
        if not pong:
            raise ValueError("PING failed")
        version = info.get("valkey_version") or info.get("redis_version", "?")
        return f"PING ok, server {version}"

    return run


def build_checks(ep: Endpoints, client: httpx.AsyncClient) -> dict[str, CheckFn]:
    return {
        "litellm-gateway": http_check(client, f"{ep.litellm_url}/health/liveliness", _status),
        "postgres": postgres_check(ep.postgres_dsn),
        "valkey": valkey_check(ep.valkey_url),
        "keycloak": http_check(
            client,
            f"{ep.keycloak_url}/realms/{ep.keycloak_realm}/.well-known/openid-configuration",
            _oidc,
        ),
        "opa": http_check(client, f"{ep.opa_url}/health", _status),
        "otel-collector": http_check(client, ep.otel_health_url, _status),
        # 200 only when initialised, unsealed and active (Step 29)
        "openbao": http_check(client, f"{ep.openbao_url}/v1/sys/health", _openbao),
    }


async def run_checks(
    checks: Mapping[str, CheckFn], per_check_seconds: float = 10.0
) -> list[CheckResult]:
    async def one(name: str, fn: CheckFn) -> CheckResult:
        start = time.perf_counter()
        try:
            detail = await asyncio.wait_for(fn(), per_check_seconds)
            ok = True
        except Exception as exc:  # report every failure, never crash the report
            detail, ok = f"{type(exc).__name__}: {exc}"[:200], False
        return CheckResult(name, ok, detail, int((time.perf_counter() - start) * 1000))

    return list(await asyncio.gather(*(one(n, f) for n, f in checks.items())))


def render(results: list[CheckResult]) -> str:
    width = max(len(r.name) for r in results)
    lines = [f"{'service'.ljust(width)}  status  ms     detail"]
    for r in results:
        status = "OK  " if r.ok else "FAIL"
        lines.append(f"{r.name.ljust(width)}  {status}    {str(r.millis).ljust(5)}  {r.detail}")
    return "\n".join(lines)


async def main() -> int:
    ep = Endpoints.from_env()
    async with httpx.AsyncClient(timeout=5.0) as client:
        results = await run_checks(build_checks(ep, client))
    print(render(results))
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
