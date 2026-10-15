"""Create the `agent-platform` client in Project 0's Keycloak realm. Idempotent.

The client is confidential, has a service account (the platform's own identity), and has the
browser login flow disabled. Per-agent service accounts and token exchange arrive in Step 29.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import httpx

CLIENT_ID = "agent-platform"


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
        "attributes": {"access.token.lifespan": "300"},
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


def provision(client: httpx.Client, base: str, realm: str, token: str) -> str:
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{base}/admin/realms/{realm}/clients"
    found = client.get(url, params={"clientId": CLIENT_ID}, headers=headers)
    found.raise_for_status()
    if found.json():
        return f"client {CLIENT_ID} in realm {realm}: exists"
    created = client.post(url, json=client_representation(), headers=headers)
    created.raise_for_status()
    return f"client {CLIENT_ID} in realm {realm}: created"


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
        print(f"keycloak: {provision(client, base, realm, token)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
