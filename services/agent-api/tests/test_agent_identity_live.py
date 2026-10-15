"""Step 29 "Done when", live against Keycloak and OpenBao (ASI03).

A throwaway agent, ``identity-probe``, is provisioned with the same functions ``just provision``
uses: a Keycloak client, its secret in OpenBao, a JWT role and a policy granting one credential
source. Then:

* the agent's service token names the agent (``azp``) and is accepted by OpenBao as that agent;
* the agent obtains the credential its spec grants, and another agent cannot;
* the agent's OpenBao token cannot read the platform's secrets;
* a user's request is delegated in two down-scoping hops: the final token is for one service,
  carries the user's identity and groups, and names the agent as the client; an agent without
  that service in its spec is refused.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from agent_contracts.models import RunContext
from agent_tools.identity import (
    AgentIdentities,
    DelegatingExchanger,
    TokenExchangeError,
    TokenExchanger,
    acting_agent,
)
from agent_tools.openbao import (
    AppRoleAuth,
    OpenBaoClient,
    OpenBaoCredentialBroker,
    OpenBaoSecretResolver,
    TokenAuth,
)

from scripts.provision import keycloak, openbao
from scripts.provision.agents import AgentNeeds

pytestmark = pytest.mark.integration

KC = os.environ.get("P0_KEYCLOAK_URL", "http://127.0.0.1:8080")
REALM = os.environ.get("P0_KEYCLOAK_REALM", "rag-platform")
TOKEN_URL = f"{KC}/realms/{REALM}/protocol/openid-connect/token"
BAO = os.environ.get("P0_OPENBAO_URL", "http://127.0.0.1:8200")
ADMIN = os.environ.get("OPENBAO_ADMIN_TOKEN", "standin-only")
SOURCE = "agent-db/creds/tool-reader"
PROBE = AgentNeeds("identity-probe", audiences=("rag-core",), credentials=(SOURCE,))
BARE = AgentNeeds("identity-bare")  # an agent whose spec grants nothing


def claims(token: str) -> dict[str, Any]:
    part = token.split(".")[1]
    return dict(json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))))


@pytest.fixture(scope="module")
def provisioned() -> Iterator[None]:
    cfg = openbao.load()
    with httpx.Client(timeout=15) as c:
        kc_token = keycloak.admin_token(
            c, KC, os.environ["KEYCLOAK_ADMIN_USER"], os.environ["KEYCLOAK_ADMIN_PASSWORD"]
        )
        headers = {"Authorization": f"Bearer {kc_token}"}
        admin = f"{KC}/admin/realms/{REALM}"
        bao = openbao.Bao(c, BAO, ADMIN)
        agents = [PROBE, BARE]
        keycloak.provision_agents(c, KC, REALM, kc_token, agents)
        clients = [f"agent-{a.name}" for a in agents]
        openbao.store_client_secrets(bao, cfg, c, admin, headers, ["agent-platform", *clients])
        jwks = f"{os.environ.get('AGENT_BAO_KEYCLOAK_URL', 'http://keycloak:8080')}/realms/{REALM}/protocol/openid-connect/certs"
        openbao.ensure_agent_identities(bao, agents, jwks, os.environ.get("AGENT_BAO_JWT_ISSUER"))
        yield
        for name in clients:
            found = c.get(f"{admin}/clients", params={"clientId": name}, headers=headers).json()
            if found:
                c.delete(f"{admin}/clients/{found[0]['id']}", headers=headers)
            for sc in c.get(f"{admin}/client-scopes", headers=headers).json():
                if sc["name"] == f"agent-platform:{name}":
                    c.delete(f"{admin}/client-scopes/{sc['id']}", headers=headers)
            bao.write(f"auth/jwt/role/{name}", method="DELETE")
            bao.write(f"sys/policies/acl/{name}", method="DELETE")
            bao.write(f"{cfg['kv_mount']}/metadata/keycloak/{name}", method="DELETE")


@pytest.fixture
async def runtime_secrets() -> AsyncIterator[OpenBaoSecretResolver]:
    """The runtime's own identity (AppRole, read-only on KV), as the services use it."""
    admin = OpenBaoClient(BAO, TokenAuth(ADMIN))
    role = "auth/approle/role/agent-platform-runtime"
    role_id = (await admin.request("GET", f"{role}/role-id"))["data"]["role_id"]
    secret = (await admin.request("POST", f"{role}/secret-id"))["data"]
    client = OpenBaoClient(BAO, AppRoleAuth(role_id, secret["secret_id"]))
    yield OpenBaoSecretResolver(client)
    await admin.request(
        "POST",
        f"{role}/secret-id-accessor/destroy",
        {"secret_id_accessor": secret["secret_id_accessor"]},
    )
    await client.aclose()
    await admin.aclose()


def ctx(agent: str) -> RunContext:
    return RunContext(tenant_id="acme", user_id="alice", agent_id=agent)


async def test_each_agent_obtains_only_the_credentials_its_spec_grants(
    provisioned: None, runtime_secrets: OpenBaoSecretResolver
) -> None:
    identities = AgentIdentities(TOKEN_URL, runtime_secrets)
    sa = claims(await identities.service_token(PROBE.name))
    assert sa["azp"] == "agent-identity-probe"
    assert "openbao" in (sa["aud"] if isinstance(sa["aud"], list) else [sa["aud"]])

    broker = OpenBaoCredentialBroker(BAO, agent_auth=identities.bao_auth)
    try:
        cred = await broker.issue(SOURCE, ctx(PROBE.name))
        assert cred.issued_to == PROBE.name
        assert cred.ttl_seconds() <= 300
        assert await broker.valid(cred)
        await broker.revoke(cred)  # the agent may end its own leases
        assert not await broker.valid(cred)
        with pytest.raises(PermissionError):
            await broker.issue(SOURCE, ctx(BARE.name))  # granted nothing
        # the agent's OpenBao identity cannot read the platform's secrets
        agent_client = OpenBaoClient(BAO, identities.bao_auth(PROBE.name))
        with pytest.raises(PermissionError):
            await OpenBaoSecretResolver(agent_client).resolve(
                "${secret:keycloak/agent-platform#client_secret}"
            )
        await agent_client.aclose()
    finally:
        await broker.aclose()
        await identities.aclose()


def _user_token(username: str) -> str:
    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "password",
            "client_id": "mcp-inspector",
            "username": username,
            "password": "standin-only",
        },
    )
    resp.raise_for_status()
    return str(resp.json()["access_token"])


async def test_user_requests_are_delegated_in_two_down_scoping_hops(
    provisioned: None, runtime_secrets: OpenBaoSecretResolver
) -> None:
    platform = TokenExchanger(
        TOKEN_URL,
        "agent-platform",
        "${secret:keycloak/agent-platform#client_secret}",
        runtime_secrets,
    )
    exchanger = DelegatingExchanger(platform, AgentIdentities(TOKEN_URL, runtime_secrets))
    user = _user_token("alice")
    try:
        reset = acting_agent.set(PROBE.name)
        try:
            token = claims((await exchanger.exchange(user, "rag-core")).access_token)
        finally:
            acting_agent.reset(reset)
        assert token["aud"] == "rag-core"
        assert token["azp"] == "agent-identity-probe"
        assert token["preferred_username"] == "alice"
        assert "/acme/finance" in token["groups"]

        # an agent whose spec has no rag-core tool cannot get a rag-core token
        reset = acting_agent.set(BARE.name)
        try:
            with pytest.raises(
                TokenExchangeError, match=r"Invalid scopes|Requested audience not available"
            ):
                await exchanger.exchange(user, "rag-core")
        finally:
            acting_agent.reset(reset)

        # a user's token cannot go to an agent directly, skipping the platform
        agent = AgentIdentities(TOKEN_URL, runtime_secrets).exchanger(PROBE.name)
        with pytest.raises(TokenExchangeError, match="not within the token audience"):
            await agent.exchange(user, "rag-core")
        await agent.aclose()
    finally:
        await exchanger.aclose()
