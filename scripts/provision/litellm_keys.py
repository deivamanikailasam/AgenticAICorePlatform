"""Create one LiteLLM virtual key per agent role, with model allowlists and budgets. Idempotent.

Keys are identified by alias ``agent-platform-<role>``. New key values are written once to a
0600 file (default ``.tmp/litellm-keys.env``, git-ignored) to be moved into OpenBao; they are
never printed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "infra" / "provision" / "litellm-keys.yaml"
ALIAS_PREFIX = "agent-platform-"


def load_roles(path: Path = CONFIG) -> tuple[dict[str, dict[str, Any]], str]:
    doc = yaml.safe_load(path.read_text())
    return doc["roles"], doc.get("budget_duration", "30d")


def existing_aliases(client: httpx.Client, base: str, master_key: str) -> set[str]:
    resp = client.get(
        f"{base}/key/list",
        params={"return_full_object": "true", "size": 100},
        headers={"Authorization": f"Bearer {master_key}"},
    )
    resp.raise_for_status()
    keys = resp.json().get("keys", [])
    return {str(k["key_alias"]) for k in keys if isinstance(k, dict) and k.get("key_alias")}


def provision(
    client: httpx.Client, base: str, master_key: str, out_file: Path, config: Path = CONFIG
) -> list[str]:
    roles, duration = load_roles(config)
    have = existing_aliases(client, base, master_key)
    actions: list[str] = []
    new_lines: list[str] = []
    for role, spec in roles.items():
        alias = f"{ALIAS_PREFIX}{role}"
        if alias in have:
            actions.append(f"key {alias}: exists")
            continue
        resp = client.post(
            f"{base}/key/generate",
            headers={"Authorization": f"Bearer {master_key}"},
            json={
                "key_alias": alias,
                "models": spec["models"],
                "max_budget": spec["max_budget_usd"],
                "budget_duration": duration,
                "metadata": {"owner": "agent-platform", "role": role},
            },
        )
        resp.raise_for_status()
        new_lines.append(f"LITELLM_KEY_{role.upper()}={resp.json()['key']}")
        actions.append(f"key {alias}: created")
    if new_lines:
        out_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(out_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write("\n".join(new_lines) + "\n")
        actions.append(f"new key values appended to {out_file} (mode 0600): move them to OpenBao")
    return actions


def main() -> int:
    base = os.environ.get("P0_LITELLM_URL", "http://127.0.0.1:4000")
    master = os.environ.get("LITELLM_MASTER_KEY")
    if not master:
        print("set LITELLM_MASTER_KEY (just with-secrets ...)")
        return 2
    out = Path(os.environ.get("LITELLM_KEYS_OUT", ROOT / ".tmp" / "litellm-keys.env"))
    with httpx.Client(timeout=20.0) as client:
        for line in provision(client, base, master, out):
            print(f"litellm: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
