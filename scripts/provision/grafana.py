"""Load Project A's dashboards into Project 0's Grafana (Step 30). Idempotent.

Each JSON file in ``infra/observability/grafana/dashboards`` goes into the folder
"Agent platform" with its fixed ``uid``; a dashboard whose content is unchanged is left alone.
The dashboards pick any Prometheus data source through their ``datasource`` variable.

Needs ``P0_GRAFANA_URL`` and an admin login (``GRAFANA_ADMIN_USER``, ``GRAFANA_ADMIN_PASSWORD``)
or a service-account token (``GRAFANA_TOKEN``).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
DASHBOARDS = ROOT / "infra" / "observability" / "grafana" / "dashboards"
FOLDER = "Agent platform"
FOLDER_UID = "agent-platform"


def _comparable(dash: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in dash.items() if k not in ("id", "version")}


def provision(client: httpx.Client, base: str) -> list[str]:
    actions: list[str] = []
    folder = client.get(f"{base}/api/folders/{FOLDER_UID}")
    if folder.status_code == 404:
        client.post(
            f"{base}/api/folders", json={"uid": FOLDER_UID, "title": FOLDER}
        ).raise_for_status()
        actions.append(f"folder {FOLDER}: created")
    else:
        folder.raise_for_status()
        actions.append(f"folder {FOLDER}: exists")
    for path in sorted(DASHBOARDS.glob("*.json")):
        dash = json.loads(path.read_text())
        current = client.get(f"{base}/api/dashboards/uid/{dash['uid']}")
        if current.status_code == 200 and _comparable(current.json()["dashboard"]) == _comparable(
            dash
        ):
            actions.append(f"dashboard {dash['uid']}: exists")
            continue
        resp = client.post(
            f"{base}/api/dashboards/db",
            json={
                "dashboard": {**dash, "id": None},
                "folderUid": FOLDER_UID,
                "overwrite": True,
                "message": "provisioned by agent-platform",
            },
        )
        resp.raise_for_status()
        actions.append(
            f"dashboard {dash['uid']}: {'updated' if current.status_code == 200 else 'created'}"
        )
    return actions


def main() -> int:
    base = os.environ.get("P0_GRAFANA_URL", "http://127.0.0.1:3000").rstrip("/")
    token = os.environ.get("GRAFANA_TOKEN")
    user, password = os.environ.get("GRAFANA_ADMIN_USER"), os.environ.get("GRAFANA_ADMIN_PASSWORD")
    if not token and not (user and password):
        print(
            "set GRAFANA_TOKEN, or GRAFANA_ADMIN_USER and GRAFANA_ADMIN_PASSWORD (just with-secrets ...)"
        )
        return 2
    kwargs: dict[str, Any] = (
        {"headers": {"Authorization": f"Bearer {token}"}} if token else {"auth": (user, password)}
    )
    with httpx.Client(timeout=15.0, **kwargs) as client:
        for line in provision(client, base):
            print(f"grafana: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
