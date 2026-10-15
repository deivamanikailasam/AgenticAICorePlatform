"""Create the `agent-platform` client in Project 0's Keycloak realm. Idempotent.

The client is confidential, has a service account (the platform's own identity), has the
browser login flow disabled, and may use standard token exchange (Keycloak 26) to obtain
down-scoped tokens for the downstream audiences in `infra/provision/keycloak.yaml`.
Per-agent service accounts arrive in Step 29.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml

CLIENT_ID = "agent-platform"
CONFIG = Path(__file__).resolve().parents[2] / "infra" / "provision" / "keycloak.yaml"


def client_representation() -> dict[str, Any]:
    return {
        "clientId": CLIENT_ID,
        "name": "Agentic AI Core Platform",
        "protocol": "openid-connect",
        "publicClient": False,
        "serviceAccountsEnabled": True,
        "standardFlowEnabled": False,
        "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "attributes": {"access.token.lifespan": "300", "standard.token.exchange.enabled": "true"},
    }


def admin_token(client: httpx.Client, base: str, user: str, password: str) -> str:
    resp = client.post(
        f"{base}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": user,
            "password": password,
        },
    )
    resp.raise_for_status()
    return str(resp.json()["access_token"])


def _scope_body(audience: str) -> dict[str, Any]:
    return {
        "name": f"{CLIENT_ID}:{audience}",
        "description": f"Tokens exchanged by {CLIENT_ID} for calls to {audience} on behalf of a user",
        "protocol": "openid-connect",
        "attributes": {"include.in.token.scope": "true", "display.on.consent.screen": "false"},
        "protocolMappers": [
            {
                "name": f"audience {audience}",
                "protocol": "openid-connect",
                "protocolMapper": "oidc-audience-mapper",
                "config": {
                    "included.client.audience": audience,
                    "access.token.claim": "true",
                    "id.token.claim": "false",
                },
            },
            {
                "name": "groups",
                "protocol": "openid-connect",
                "protocolMapper": "oidc-group-membership-mapper",
                "config": {
                    "claim.name": "groups",
                    "full.path": "true",
                    "access.token.claim": "true",
                    "id.token.claim": "false",
                    "userinfo.token.claim": "false",
                },
            },
        ],
    }


def provision(
    client: httpx.Client, base: str, realm: str, token: str, audiences: list[str] | None = None
) -> list[str]:
    headers = {"Authorization": f"Bearer {token}"}
    admin = f"{base}/admin/realms/{realm}"
    actions: list[str] = []
    found = client.get(f"{admin}/clients", params={"clientId": CLIENT_ID}, headers=headers)
    found.raise_for_status()
    if found.json():
        current = found.json()[0]
        if current.get("attributes", {}).get("standard.token.exchange.enabled") != "true":
            current.setdefault("attributes", {})["standard.token.exchange.enabled"] = "true"
            client.put(
                f"{admin}/clients/{current['id']}", json=current, headers=headers
            ).raise_for_status()
            actions.append(f"client {CLIENT_ID}: token exchange enabled")
        else:
            actions.append(f"client {CLIENT_ID} in realm {realm}: exists")
    else:
        client.post(
            f"{admin}/clients", json=client_representation(), headers=headers
        ).raise_for_status()
        actions.append(f"client {CLIENT_ID} in realm {realm}: created")
    cid = client.get(f"{admin}/clients", params={"clientId": CLIENT_ID}, headers=headers).json()[0][
        "id"
    ]

    for audience in audiences if audiences is not None else load_audiences():
        name = f"{CLIENT_ID}:{audience}"
        scopes = client.get(f"{admin}/client-scopes", headers=headers).json()
        scope = next((sc for sc in scopes if sc["name"] == name), None)
        if scope is None:
            client.post(
                f"{admin}/client-scopes", json=_scope_body(audience), headers=headers
            ).raise_for_status()
            scope = next(
                sc
                for sc in client.get(f"{admin}/client-scopes", headers=headers).json()
                if sc["name"] == name
            )
            actions.append(f"client scope {name}: created")
        else:
            actions.append(f"client scope {name}: exists")
        optional = client.get(
            f"{admin}/clients/{cid}/optional-client-scopes", headers=headers
        ).json()
        if not any(o["id"] == scope["id"] for o in optional):
            client.put(
                f"{admin}/clients/{cid}/optional-client-scopes/{scope['id']}", headers=headers
            ).raise_for_status()
            actions.append(f"client scope {name}: attached to {CLIENT_ID}")
    return actions


def load_audiences(path: Path = CONFIG) -> list[str]:
    return list(yaml.safe_load(path.read_text()).get("downstream_audiences", []))


def main() -> int:
    base = os.environ.get("P0_KEYCLOAK_URL", "http://127.0.0.1:8080")
    realm = os.environ.get("P0_KEYCLOAK_REALM", "rag-platform")
    user, password = (
        os.environ.get("KEYCLOAK_ADMIN_USER"),
        os.environ.get("KEYCLOAK_ADMIN_PASSWORD"),
    )
    if not user or not password:
        print("set KEYCLOAK_ADMIN_USER and KEYCLOAK_ADMIN_PASSWORD (just with-secrets ...)")
        return 2
    with httpx.Client(timeout=10.0) as client:
        token = admin_token(client, base, user, password)
        for line in provision(client, base, realm, token):
            print(f"keycloak: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
