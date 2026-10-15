#!/usr/bin/env bash
# Build the read-only root filesystem for Firecracker sandbox VMs from the same digest-pinned
# Python image the container engines use, with guest_init.py as /sbin/agent-init. Needs Docker,
# e2fsprogs and root (file ownership inside the image must be root's).
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="${AGENT_FIRECRACKER_DIR:-/var/lib/agent-platform/firecracker}"
IMAGE="$(python3 -c 'import ast,sys; t=ast.parse(open("services/sandbox-runner/src/sandbox_runner/engines.py").read()); print(next(n.value.value for n in t.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "DEFAULT_IMAGE"))')"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
docker pull -q "$IMAGE" >/dev/null
cid="$(docker create "$IMAGE")"
docker export "$cid" | tar -x -C "$tmp" --numeric-owner
docker rm "$cid" >/dev/null
install -m 0755 services/sandbox-runner/src/sandbox_runner/guest_init.py "$tmp/sbin/agent-init"
mkdir -p "$tmp/work" "$tmp/proc" "$tmp/sys" "$tmp/dev" "$tmp/tmp"
rm -f "$tmp/.dockerenv"
install -d -m 0755 "$DEST"
truncate -s 512M "$tmp.ext4"
mkfs.ext4 -q -F -L agent-rootfs -d "$tmp" "$tmp.ext4"
mv "$tmp.ext4" "$DEST/rootfs.ext4"
chmod 0644 "$DEST/rootfs.ext4"
echo "rootfs: $DEST/rootfs.ext4 (from $IMAGE)"
