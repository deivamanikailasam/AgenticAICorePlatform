#!/usr/bin/env bash
# Install Firecracker and the pinned guest kernel for the microVM sandbox engine (ADR 0021).
# Linux with KVM only: CI runners, a Lima VM with nested virtualisation on an Apple M3+ Mac,
# Kubernetes nodes. Every download is verified by SHA-256. Needs root.
#
#   Firecracker v1.17.0 — release tarball checksums published with the release
#   kernel 6.1.188     — Firecracker CI build 20261007-f23a2136011e-0 (no published checksum:
#                        pinned to the SHA-256 recorded on 2026-10-15)
set -euo pipefail
VERSION="${FIRECRACKER_VERSION:-v1.17.0}"
DEST="${AGENT_FIRECRACKER_DIR:-/var/lib/agent-platform/firecracker}"
ARCH="$(uname -m)"
CI="https://s3.amazonaws.com/spec.ccfc.min/firecracker-ci/20261007-f23a2136011e-0"
case "$ARCH" in
  x86_64)  FC_SHA=06094a1108ae9e82aa4c23a775aa92758f53f1175d422270d9d6162cb9ade558
           K_SHA=ca1cfce95d2c0c3cd644a4f868162f8a3a36880bf7e22351b5f21729516e5f1c ;;
  aarch64) FC_SHA=e351ebe4f7a16b5873bbd51005d2e6767103cff4d5ebc829df2d3f95a93e2256
           K_SHA=e7ebee4166387866da034b6c64d33ce8896cc0a087eb22034a8093c44c8973f0 ;;
  *) echo "unsupported architecture $ARCH" >&2; exit 1 ;;
esac
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
curl -fsSL -o "$tmp/fc.tgz" \
  "https://github.com/firecracker-microvm/firecracker/releases/download/${VERSION}/firecracker-${VERSION}-${ARCH}.tgz"
echo "${FC_SHA}  $tmp/fc.tgz" | sha256sum -c -
tar -xzf "$tmp/fc.tgz" -C "$tmp"
install -m 0755 "$tmp/release-${VERSION}-${ARCH}/firecracker-${VERSION}-${ARCH}" /usr/local/bin/firecracker
install -m 0755 "$tmp/release-${VERSION}-${ARCH}/jailer-${VERSION}-${ARCH}" /usr/local/bin/jailer
curl -fsSL -o "$tmp/vmlinux" "${CI}/${ARCH}/vmlinux-6.1.188"
echo "${K_SHA}  $tmp/vmlinux" | sha256sum -c -
install -d -m 0755 "$DEST"
install -m 0644 "$tmp/vmlinux" "$DEST/vmlinux"
firecracker --version | head -1
echo "kernel: $DEST/vmlinux; next: scripts/build_sandbox_rootfs.sh"
