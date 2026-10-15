# ADR 0019: Code sandbox — gVisor by default, Kata as an option, Firecracker and managed as stubs

- **Status:** Accepted; points 3–4 and the open question superseded by [ADR 0021](0021-sandbox-engines-decision.md)
- **Date:** 2026-10-15
- **Step:** 09
- **Deciders:** repository owner (decision requested on the stubs — see "Open question")

## Context
Agents may write and run code (OWASP ASI05). The spec's default is gVisor-isolated containers;
the catalogue lists Firecracker, Kata Containers and a managed sandbox service as alternatives.
This build runs in a cloud container with Docker but **without KVM** (`/dev/kvm` absent, no
`vmx`/`svm` CPU flags) and without any sandbox vendor account.

## Decision
1. **Interface.** A 24th slot, `code_sandbox`, with a contract that kernel-isolated engines must
   pass (hello world, failure as status, no network, time limit, files in/out, output cap) and
   that test doubles pass by never executing code.
2. **Default: gVisor** (`runsc`, release 20261005.0, SHA-512 verified, `--platform=systrap`),
   driven through the Docker CLI with a hardened profile (see the change entry). Verified here.
3. **Kata Containers**: same engine, OCI runtime `kata-runtime`; code-complete, contract runs
   wherever Kata is installed; **not verified in this build** (needs KVM).
4. **Firecracker and managed sandbox: documented stubs** that always refuse. Firecracker needs
   KVM plus a jailer and guest image; a managed service needs a vendor choice, a contract and
   credentials. Neither can be built and tested honestly here.
5. **Refuse, never downgrade.** An engine whose runtime is unavailable returns `refused`; there
   is no fallback to plain `runc`.
6. **Host process.** `sandbox-runner` runs on the host (or, in Kubernetes, will launch pods with a
   gVisor `RuntimeClass` — Step 38), never as a container holding the Docker socket.
7. **Files** move as a tar stream over stdin/stdout; the sandbox sees no host path.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Plain Docker (runc) with seccomp | Shares the host kernel; one kernel bug escapes |
| Python-level sandboxes (RestrictedPython, subinterpreters) | Bypassable; not a security boundary |
| WebAssembly (Pyodide) | Strong isolation, but limited libraries; a good future engine |
| Firecracker now | Needs KVM; cannot be verified here |

## Consequences
gVisor adds startup latency (about 1 s per run here) and does not support every syscall.
On the Mac, gVisor inside OrbStack is unconfirmed (VERIFY-ON-MAC); if unavailable, the sandbox
refuses rather than running unsafely.

## Open question for the owner
The spec does not mark Firecracker or the managed sandbox as stubs. Options: (a) accept them as
stubs until KVM hardware and a vendor are available; (b) provide a KVM-capable host for
Firecracker and Kata verification; (c) name a managed sandbox vendor and provide credentials.

## Verification
`just test-sandbox` (CI job `sandbox` installs gVisor and runs it).
