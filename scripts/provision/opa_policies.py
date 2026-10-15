"""Load every non-test ``policies/**/*.rego`` and ``policies/**/data.json`` into OPA. Idempotent (PUT).

For local development and CI. In staging and production OPA pulls signed bundles (Step 38).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    url = os.environ.get("P0_OPA_URL", "http://127.0.0.1:8181").rstrip("/")
    files = sorted(
        p for p in (ROOT / "policies").rglob("*.rego") if not p.name.endswith("_test.rego")
    )
    with httpx.Client(timeout=10) as client:
        for f in files:
            pid = "agent-platform/" + str(f.relative_to(ROOT / "policies")).removesuffix(".rego")
            resp = client.put(
                f"{url}/v1/policies/{pid}",
                content=f.read_bytes(),
                headers={"Content-Type": "text/plain"},
            )
            if resp.status_code >= 300:
                print(f"opa: {pid}: HTTP {resp.status_code} {resp.text[:200]}")
                return 1
            print(f"opa: policy {pid}: loaded")
        for f in sorted((ROOT / "policies").rglob("data.json")):
            path = str(f.parent.relative_to(ROOT / "policies"))
            resp = client.put(
                f"{url}/v1/data/{path}",
                content=f.read_bytes(),
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code >= 300:
                print(f"opa: data {path}: HTTP {resp.status_code} {resp.text[:200]}")
                return 1
            print(f"opa: data {path}: loaded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
