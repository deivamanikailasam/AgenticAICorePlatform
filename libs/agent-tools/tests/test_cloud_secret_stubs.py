"""Cloud secret managers, cloud IAM brokers and SPIFFE workload identity are documented stubs:
they are registered, refuse every call and point to ADR 0040."""

from typing import Any

import httpx
import pytest
from agent_contracts.models import Credential, RunContext, utcnow
from agent_contracts.registry import registry as default_registry
from agent_tools.cloud_secrets import (
    AwsSecretsManagerResolver,
    AzureKeyVaultResolver,
    GcpSecretManagerResolver,
    SpiffeJwtAuth,
)

CTX = RunContext(tenant_id="t", user_id="u", agent_id="a")


@pytest.mark.parametrize("name", ["aws", "gcp", "azure"])
async def test_cloud_brokers_are_registered_stubs_that_refuse(name: str) -> None:
    broker: Any = default_registry.create("credential_broker", name)
    assert broker.component_status == "stub"
    with pytest.raises(NotImplementedError, match=r"documented stub \(ADR 0040\)"):
        await broker.issue("db/creds/r", CTX)
    cred = Credential(lease_id="l", source="s", expires_at=utcnow())
    with pytest.raises(NotImplementedError, match="ADR 0040"):
        await broker.revoke(cred)


@pytest.mark.parametrize(
    "resolver", [AwsSecretsManagerResolver, GcpSecretManagerResolver, AzureKeyVaultResolver]
)
async def test_cloud_resolvers_refuse(resolver: Any) -> None:
    with pytest.raises(NotImplementedError, match="ADR 0040"):
        await resolver().resolve("${secret:a#b}")


async def test_spiffe_login_refuses() -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(NotImplementedError, match="SPIFFE/SPIRE"):
            await SpiffeJwtAuth("agent-triage").login(http, "http://bao")


def test_openbao_and_vault_are_implemented() -> None:
    for name in ("openbao", "vault"):
        assert default_registry.get("credential_broker", name).component_status == "implemented"
