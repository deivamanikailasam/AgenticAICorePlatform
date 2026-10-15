# ADR 0021: Sandbox engines — Firecracker and Kata implemented and verified on KVM; managed service stays a stub

- **Status:** Accepted (amends ADR 0019, "Open question")
- **Date:** 2026-10-15
- **Step:** 09 (revisited after Step 10)
- **Deciders:** repository owner delegated the decision ("make the best decision")

## Context
ADR 0019 left Firecracker and the managed sandbox as stubs because the build container has no
KVM, and asked the owner to choose. The owner delegated the choice and noted the development
machine: a MacBook Pro with an Apple M5 Max and 128 GB of unified memory.

Facts checked on 2026-10-15:
- **Mac:** Lima's own template documents `nestedVirtualization: true` as "supported with
  `vmType: vz` … on Apple M3 or later (macOS 15 or later)". An M5 Max therefore can expose
  `/dev/kvm` to a Linux VM. Firecracker supports aarch64 Linux.
- **CI:** GitHub's changelog (2024-04-02) documents hardware acceleration on its standard Linux
  runners, enabled with a udev rule that opens `/dev/kvm` to the runner user.
- **Versions:** Firecracker v1.17.0 (release checksums published), guest kernel 6.1.188 from
  Firecracker CI build 20261007-f23a2136011e-0 (no published checksum; SHA-256 recorded),
  Kata Containers 4.2.0 static release (no published checksum; SHA-256 recorded).

## Decision
1. **gVisor stays the default.** It needs no KVM, starts fastest and already runs everywhere
   the platform runs, including Docker on the Mac (via OrbStack's Linux VM; VERIFY-ON-MAC).
2. **Firecracker: implement fully, now.** It is the strongest boundary in the catalogue (a
   separate kernel *and* a separate virtual machine with a tiny device model), it runs on both
   x86_64 (CI, cloud) and aarch64 (the Mac via Lima), and it can be verified honestly on
   GitHub's KVM runners. Choose it for untrusted code with high blast radius or strict tenant
   separation.
3. **Kata Containers: verify, now.** Same reasons; the engine already existed. It gives VM
   isolation with the Docker/OCI interface unchanged. CI installs it on the same KVM runner.
4. **Managed sandbox service: remains a documented stub, by decision.** Using one means sending
   model-written code, and often tenant data, to a third party. That needs a named vendor, a
   data-processing agreement, Australian data residency and a security review; none exists, and
   two self-hosted VM engines already cover the need. An adapter written against an unverified
   vendor API would be invented, which the build rules forbid. Revisit at Step 40 (cloud move)
   if a vendor is approved.
5. **"Not available" fails in the jobs that install an engine.** `AGENT_REQUIRE_ENGINES` turns
   skips into failures, so a green CI run proves the engine really ran.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Keep Firecracker and Kata as stubs | Leaves the strongest isolation unbuilt although CI and the Mac can run it |
| Implement a managed vendor adapter now | Unverifiable without a vendor and credentials; data-residency and privacy questions unanswered |
| Firecracker through Kata (`configuration-rs-fc.toml`) only | One engine instead of two; direct Firecracker gives full control of the guest and the smallest stack |
| Ignite / firecracker-containerd | Extra moving parts for no added isolation |

## Consequences
- Each Firecracker run boots a VM; on KVM this adds start-up time (measured by the CI job, not
  claimed here). Use it where isolation matters more than latency.
- The guest kernel and Kata archive are pinned to recorded checksums (trust on first use);
  re-record them deliberately when upgrading.
- Production must also run Firecracker under its `jailer` (chroot, namespaces, cgroups); this
  belongs to the Kubernetes work in Step 38.
- On the Mac, Firecracker and Kata need a Lima VM with `vmType: vz` and
  `nestedVirtualization: true` (docs/setup/local.md); VERIFY-ON-MAC.

## Verification
CI job "sandbox (Firecracker, Kata; KVM)" runs the full `CodeSandboxContract` and the hostile
cases (fork bomb, ten-minute loop, memory hog, host filesystem) on both engines;
`services/sandbox-runner/tests/test_firecracker.py` covers the parts that need no KVM.
