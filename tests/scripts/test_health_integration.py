"""Runs against Project 0 on the Mac, or the stand-in stack in CI (`just standin-up`)."""

import httpx
import pytest

from scripts.health import Endpoints, build_checks, run_checks

pytestmark = pytest.mark.integration


async def test_every_project0_dependency_is_reachable() -> None:
    async with httpx.AsyncClient(timeout=5.0) as client:
        results = await run_checks(build_checks(Endpoints.from_env(), client))
    failed = {r.name: r.detail for r in results if not r.ok}
    assert not failed, failed
    assert {r.name for r in results} == {
        "litellm-gateway",
        "postgres",
        "valkey",
        "keycloak",
        "opa",
        "otel-collector",
        "openbao",
    }
