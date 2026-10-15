# The sandbox can now use real micro virtual machines: Firecracker

**Step:** 09 (revisited) · **Commit:** feat(sandbox-runner): implement the Firecracker microVM engine · **Date:** 2026-10-15

## What changed
The Firecracker engine was a stub that always refused. It now runs each program in its own
Firecracker microVM (v1.17.0) with its own Linux kernel (6.1.188):
- **No network device** at all; a **read-only root** built from the same digest-pinned Python
  image the container engines use; a fresh **work disk** per run that carries the program and
  input files in and the results out. The host writes and reads that disk with `mkfs.ext4` and
  `debugfs`, so it never mounts anything and needs no root.
- Inside the VM a small init (`guest_init.py`) runs the program as `nobody` with process and
  file limits, a memory and process cgroup when the kernel has one, and a wall clock. The VM
  size caps memory, and the host kills the VM if the guest overruns.
- `scripts/install_firecracker.sh` installs the binary and kernel (SHA-256 checked, x86_64 and
  aarch64); `scripts/build_sandbox_rootfs.sh` builds the root filesystem.
- New CI job `microvm` turns on KVM on GitHub's runner, installs everything and runs the full
  sandbox contract plus the hostile cases on Firecracker. `AGENT_REQUIRE_ENGINES` makes "engine
  not available" a failure there instead of a skip.
- The managed-service engine stays a documented stub, now by decision (ADR 0021).
- Fix found on the way: the gateway unit tests built a Valkey rate limiter whenever `.env` set
  `P0_VALKEY_URL` (as `just` loads it), so they failed without a running Valkey. They now inject
  an in-memory limiter.

## Why
gVisor gives each program a separate kernel written in user space; a microVM gives it a
separate *machine*. Like moving a dangerous experiment from a sealed glovebox into its own
building: more work to set up, and a much thicker wall.

## How to check it
`uv run pytest services/sandbox-runner/tests/test_firecracker.py` (VM config, work-disk round
trip, guest init logic, result mapping). On a Linux host with KVM: run the two scripts, then
`just test-microvm`. In CI: job "sandbox (Firecracker; KVM)".

## Risks and follow-ups
The kernel has no published checksum; it is pinned to the SHA-256 recorded on 2026-10-15.
Production should also run Firecracker under its `jailer` (chroot, namespaces, cgroups); that is
part of the Kubernetes work in Step 38. On the Mac it needs a Lima VM with nested virtualisation
(VERIFY-ON-MAC).
