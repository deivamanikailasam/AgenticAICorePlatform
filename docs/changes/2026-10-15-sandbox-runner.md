# Run model-written code in a throwaway gVisor box with no network

**Step:** 09 · **Commit:** feat(sandbox-runner): add gVisor-isolated code execution and the sandbox tool · **Date:** 2026-10-15

## What changed
- **Engines.** `code_sandbox.gvisor` (default) runs each program in a fresh container under
  gVisor's user-space kernel (`runsc`): no network, read-only root, a size-capped tmpfs work
  area, no host mounts, all capabilities dropped, an unprivileged user, CPU/memory/swap/PID
  limits, and a time limit enforced inside and outside the container. Files go in and out as a
  tar stream, so the box never sees a host path. `code_sandbox.kata` is the same engine on the
  Kata VM runtime (needs KVM). `firecracker` and `managed` are documented stubs that refuse
  (ADR 0019). An engine whose runtime is missing **refuses**; it never falls back to plain runc.
- **Service.** `sandbox-runner` (`just sandbox-runner`, 127.0.0.1:8095) exposes
  `POST /v1/executions`; inputs may be S3 artefacts and outputs are written back to S3. Callers
  need a Keycloak token. It runs on the host, because giving a container the Docker socket would
  give it root on the host.
- **Tool.** `sandbox://python` (`SandboxPythonTool`) lets agents use it.
- **CI.** A new job installs the pinned gVisor release (20261005.0, SHA-512 checked) with
  `scripts/install_gvisor.sh` and runs `just test-sandbox`.

## Why
A model asked to "analyse this CSV" will write code, and some of that code will be wrong or
hostile (OWASP ASI05). The sandbox is a blast chamber: whatever happens inside stays inside, and
the result says what happened.

## How to check it
`just test-sandbox` (Docker + runsc). Observed in this build (gVisor 20261005.0): a fork bomb →
`killed` in about 1 s with a reason; a network call → `blocked … Network is unreachable`; a
10-minute sleep with a 3 s limit → `timeout` after about 3.9 s; a 1 GiB allocation with a 64 MiB
limit → `oom`; code reports kernel `4.19.0-gvisor`; `/home/user` and the Docker socket are not
visible.

## Risks and follow-ups
gVisor on the Mac (OrbStack) is not confirmed (VERIFY-ON-MAC). Kata is code-complete but not
verified here (no KVM). The fork bomb's exact status varies with where the process limit bites
(it is always non-OK and contained).
