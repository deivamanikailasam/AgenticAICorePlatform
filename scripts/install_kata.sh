#!/usr/bin/env bash
# Install Kata Containers 4.2.0 (static release) and register it with Docker as the
# "kata-runtime" runtime for the Kata sandbox engine. Linux with KVM only. Needs root and zstd.
# The release publishes no checksum file: the SHA-256 values were recorded on 2026-10-15.
set -euo pipefail
VERSION="${KATA_VERSION:-4.2.0}"
case "$(uname -m)" in
  x86_64)  ARCH=amd64; SHA=b828904fa3f1e49ddd7dc799c72cb1503cd1e772d354c3987c8d4189b2a623a8 ;;
  aarch64) ARCH=arm64; SHA=5dd4e9f2d5ea9e6bdfa2f476b3315335b58252366fcda2775a3094fc8fec376b ;;
  *) echo "unsupported architecture $(uname -m)" >&2; exit 1 ;;
esac
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
curl -fsSL -o "$tmp/kata.tar.zst" \
  "https://github.com/kata-containers/kata-containers/releases/download/${VERSION}/kata-static-${VERSION}-${ARCH}.tar.zst"
echo "${SHA}  $tmp/kata.tar.zst" | sha256sum -c -
tar --zstd -xf "$tmp/kata.tar.zst" -C /
ln -sf /opt/kata/runtime-rs/bin/containerd-shim-kata-v2 /usr/local/bin/containerd-shim-kata-v2
mkdir -p /etc/docker
python3 - <<'PY'
import json, os
path = "/etc/docker/daemon.json"
cfg = json.load(open(path)) if os.path.exists(path) else {}
cfg.setdefault("runtimes", {})["kata-runtime"] = {"runtimeType": "io.containerd.kata.v2"}
json.dump(cfg, open(path, "w"), indent=2)
PY
echo "Kata ${VERSION} installed; restart dockerd to load the kata-runtime runtime"
