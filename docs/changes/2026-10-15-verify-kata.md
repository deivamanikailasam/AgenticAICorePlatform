# Kata Containers is now installed and tested, not just written

**Step:** 09 (revisited) · **Commit:** ci: install and verify the Kata engine on the KVM runner · **Date:** 2026-10-15

## What changed
`scripts/install_kata.sh` installs Kata Containers 4.2.0 (static release, SHA-256 checked for
x86_64 and arm64) and registers it with Docker as the `kata-runtime` runtime through Kata's
containerd shim. The CI job on the KVM runner now installs Kata too and requires both engines
(`AGENT_REQUIRE_ENGINES=firecracker,kata`): the sandbox contract and the hostile cases must
pass on Kata, and "not available" fails the job.

## Why
The Kata engine existed since Step 9 but had never run anywhere, because the build container
has no KVM. Code that has never run is a guess; now it is proven on every push.

## How to check it
CI job "sandbox (Firecracker, Kata; KVM)". On a KVM host: `sudo ./scripts/install_kata.sh`,
restart Docker, then `AGENT_REQUIRE_ENGINES=kata just test-microvm`.

## Risks and follow-ups
Kata's release publishes no checksum file; the archive is pinned to the SHA-256 recorded on
2026-10-15. Each Kata run starts a small VM, so it is slower than gVisor.
