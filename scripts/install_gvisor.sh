#!/usr/bin/env bash
# Install gVisor (runsc) and register it as a Docker runtime. Linux hosts only (CI, dev VMs,
# Kubernetes nodes). Pinned release, verified by SHA-512. Needs root.
set -euo pipefail
RELEASE="${GVISOR_RELEASE:-20261005.0}"
SHA512="${GVISOR_SHA512:-79869ae9a589355a46d46fdac068d9a954e741741ffe77c64e38c56ea955498c1cb04e9bce44e1cce5155333e6b76bee72347c6661bb7c78fd2fe3aad198c8fe}"
URL="https://storage.googleapis.com/gvisor/releases/release/${RELEASE}/$(uname -m)/gvisor.tar.bz2"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
curl -fsSL -o "$tmp/gvisor.tar.bz2" "$URL"
echo "${SHA512}  $tmp/gvisor.tar.bz2" | sha512sum -c -
tar -xjf "$tmp/gvisor.tar.bz2" -C "$tmp"
install -m 0755 "$tmp/runsc" "$tmp/containerd-shim-runsc-v1" /usr/local/bin/
rm -rf /usr/local/bin/gvisor-bin && cp -r "$tmp/gvisor-bin" /usr/local/bin/gvisor-bin
mkdir -p /etc/docker
python3 - <<'PY'
import json, os
path = "/etc/docker/daemon.json"
cfg = json.load(open(path)) if os.path.exists(path) else {}
cfg.setdefault("runtimes", {})["runsc"] = {"path": "/usr/local/bin/runsc", "runtimeArgs": ["--platform=systrap"]}
json.dump(cfg, open(path, "w"), indent=2)
PY
runsc --version | head -1
echo "gVisor installed; restart dockerd to load the runsc runtime"
