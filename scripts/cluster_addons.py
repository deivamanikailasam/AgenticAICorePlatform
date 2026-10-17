"""Fetch the cluster add-ons' release manifests and check them against pinned SHA-256s (Step 38).

    just cluster-addons     # writes deploy/argocd/install/upstream.yaml and deploy/keda/upstream.yaml

Then: kustomize build deploy/argocd/install | kubectl apply --server-side -f -   (and deploy/keda)
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADDONS = {
    "deploy/argocd/install/upstream.yaml": (
        "https://raw.githubusercontent.com/argoproj/argo-cd/v3.5.3/manifests/install.yaml",
        "7efe2d6bbc03f63623640f1e4198f16c84009d510fb810ef71e56df1b7614ba9",
    ),
    "deploy/keda/upstream.yaml": (
        "https://github.com/kedacore/keda/releases/download/v2.21.0/keda-2.21.0.yaml",
        "b43c89ffeef81722d7e2dd2c079d74789767a0f89cae1336cff784994814f6d7",
    ),
}


def main() -> int:
    for path, (url, sha) in ADDONS.items():
        target = ROOT / path
        data = target.read_bytes() if target.exists() else b""
        if hashlib.sha256(data).hexdigest() != sha:
            with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 - pinned https URL
                data = resp.read()
            got = hashlib.sha256(data).hexdigest()
            if got != sha:
                print(f"cluster-addons: {url} has sha256 {got}, expected {sha}", file=sys.stderr)
                return 1
            target.write_bytes(data)
        print(f"cluster-addons: {path} ok ({sha[:12]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
