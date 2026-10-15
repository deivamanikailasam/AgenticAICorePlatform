"""Create the OpenFGA store and write the authorisation model if it changed. Idempotent.

Only for deployments that use relationship checks (``MCP_GATEWAY_PDP=opa+openfga``); run after
``just up-alternatives``. The model is ``policies/openfga/model.json``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "policies/openfga/model.json"


def _strip(value: Any) -> Any:
    """Drop the empty defaults OpenFGA adds when it stores a model ("", null, {})."""
    if isinstance(value, dict):
        out = {k: _strip(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if v not in ("", None, {})}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    def canon(m: dict[str, Any]) -> str:
        return json.dumps(
            _strip({k: m.get(k) for k in ("schema_version", "type_definitions", "conditions")}),
            sort_keys=True,
        )

    return canon(a) == canon(b)


def provision(client: httpx.Client, store: str, model: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    found = [
        s
        for s in client.get("/stores", params={"name": store}).raise_for_status().json()["stores"]
        if s["name"] == store
    ]
    if found:
        store_id = found[0]["id"]
        actions.append(f"store {store}: exists")
    else:
        store_id = client.post("/stores", json={"name": store}).raise_for_status().json()["id"]
        actions.append(f"store {store}: created")
    latest = (
        client.get(f"/stores/{store_id}/authorization-models", params={"page_size": 1})
        .raise_for_status()
        .json()
    )
    current = (latest.get("authorization_models") or [None])[0]
    if current is not None and _same(current, model):
        actions.append(f"model: up to date ({current['id']})")
    else:
        model_id = (
            client.post(f"/stores/{store_id}/authorization-models", json=model)
            .raise_for_status()
            .json()["authorization_model_id"]
        )
        actions.append(f"model: written ({model_id})")
    return actions


def main() -> int:
    url = os.environ.get("AGENT_OPENFGA_URL", "http://127.0.0.1:8085").rstrip("/")
    key = os.environ.get("SECRET__OPENFGA__AGENT_PLATFORM__PRESHARED_KEY")
    if not key:
        print("set SECRET__OPENFGA__AGENT_PLATFORM__PRESHARED_KEY (just with-secrets ...)")
        return 2
    store = os.environ.get("AGENT_OPENFGA_STORE", "agent-platform")
    with httpx.Client(base_url=url, timeout=10, headers={"Authorization": f"Bearer {key}"}) as c:
        for line in provision(c, store, json.loads(MODEL.read_text())):
            print(f"openfga: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
