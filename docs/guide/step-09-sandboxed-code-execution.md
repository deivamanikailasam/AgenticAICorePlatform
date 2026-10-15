# Step 9 · Sandboxed code execution

## 1. At a glance

| | |
|---|---|
| **Goal** | Run model-written code where it cannot hurt anything, and report what happened |
| **Time** | About a day |
| **You end up with** | The `code_sandbox` interface and contract, `services/sandbox-runner` with gVisor (default) and Kata engines, Firecracker and managed stubs, the `sandbox://python` tool, a gVisor installer and a CI job |
| **New words** | sandbox, gVisor, OCI runtime, tmpfs, cgroup limit, fork bomb |

## 2. The big idea

A bomb-disposal chamber. Whatever the code does — loops forever, forks endlessly, tries to call
the internet, eats memory — happens inside a disposable box with thick walls (a separate kernel),
no windows (no network) and a timer. When it ends, the box is thrown away and a report says what
happened.

> *Analogy:* a test kitchen with its own gas supply and fire suppression: a disaster in there
> never reaches the restaurant.

## 3. What we're doing

1. Define `CodeSandbox` and its contract.
2. Run code in fresh gVisor containers with a hardened profile.
3. Expose it as an HTTP service with S3 artefacts and Keycloak auth.
4. Give agents a `sandbox://python` tool.
5. Prove containment of a fork bomb, a network call, a 10-minute loop and a memory hog.

## 4. Why we're doing this

Code interpreters are among the most useful agent tools — and the riskiest (OWASP ASI05). A
normal container shares the host's kernel; gVisor puts its own user-space kernel in between, so
the code talks to gVisor, not to Linux directly.

> *Watch out:* never "fix" a missing gVisor by running plain Docker. The engine refuses instead.

## 5. Key ideas

- **gVisor (`runsc`).** An OCI runtime whose "kernel" runs in user space. Inside, `uname -r`
  reports `4.19.0-gvisor`.
- **Hardened profile.** `--network=none`, read-only root, tmpfs `/work` with a size cap, no host
  mounts, `--cap-drop=ALL`, `no-new-privileges`, user `65534`, memory/swap/CPU/PID limits.
- **Two clocks.** `timeout -s KILL` inside the box and a runner-side deadline outside it.
- **Tar in, tar out.** Inputs arrive on stdin as a tar; results (stdout, stderr, exit code,
  `/work/out`) leave on stdout as a tar.
- **Refuse, never downgrade.**

## 6. How to do it

Linux host or CI:

```bash
sudo ./scripts/install_gvisor.sh && sudo systemctl restart docker   # pinned 20261005.0, SHA-512 checked
docker info --format '{{json .Runtimes}}'                           # includes "runsc"
just test-sandbox                                                    # contract + hostile cases
just sandbox-runner                                                  # API on 127.0.0.1:8095
```

On the Mac: install gVisor inside the OrbStack Linux machine and register it with OrbStack's
Docker engine (VERIFY-ON-MAC); without it the sandbox refuses.

Using the tool from an agent spec:

```yaml
tools:
  - ref: sandbox://python
```

## 7. Benefits

- Agents can compute, parse and transform data without risking the host.
- Every outcome is a status with a reason: `ok`, `error`, `timeout`, `oom`, `killed`, `refused`.
- Inputs and outputs live in S3 as artefacts, so runs are reproducible and auditable.

## 8. Drawbacks and risks

- About one second of start-up per run under gVisor here.
- Some syscalls are unsupported in gVisor; rare libraries may fail.
- The runner needs Docker access on the host; protect that host.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| gVisor | Kata Containers | KVM hosts needing full VM isolation; implemented, needs KVM |
| gVisor | Firecracker | Very high volume with fast microVM boot; stub until KVM is available |
| gVisor | Managed sandbox service | No infrastructure to run; stub until a vendor is chosen |
| Docker CLI | Kubernetes pods with `RuntimeClass: gvisor` | Production (Step 38) |

## 10. Which approach fits which situation

- **Laptop / CI:** gVisor via Docker.
- **Kubernetes production:** gVisor RuntimeClass (GKE Sandbox on Google Cloud), sandbox pods on a
  dedicated node pool.
- **Strict multi-tenant isolation on KVM hardware:** Kata or Firecracker.

## 11. Common mistakes

- Expecting network access for `pip install` inside the sandbox: there is none, by design.
- Writing results anywhere but `/work/out`: only that folder comes back.
- Mounting a host folder "just for testing": it defeats the boundary; use input files.

## 12. Done when

| Check | Status | Evidence (gVisor 20261005.0, this build) |
|---|---|---|
| A fork bomb is contained and reported | ✅ | status `killed` in about 1 s with a reason; a follow-up run succeeds |
| A network call is contained and reported | ✅ | `blocked OSError [Errno 101] Network is unreachable` |
| A 10-minute loop is contained and reported | ✅ | `timeout` after about 3.9 s with a 3 s limit |
| Results come back as artefacts | ✅ | `test_files_come_from_and_go_to_the_artefact_store`, `test_files_in_and_out` |
| No host mounts | ✅ | `/home/user` and the Docker socket are not visible inside |
| Alternatives | ⚠️ | Kata implemented, not verified (no KVM); Firecracker and managed are stubs (ADR 0019, open question) |
| On the Mac | ⚠️ **VERIFY-ON-MAC** | gVisor inside OrbStack |
| ADR | ✅ | ADR 0019 |

## 13. Recap

Step 9 added a sandbox socket and contract, and a sandbox runner that executes each program in a
fresh gVisor container with no network, no host mounts, a read-only root, capped resources and
two clocks; files travel as tar streams and results return as S3 artefacts. Hostile code — a fork
bomb, a network call, a ten-minute loop and a memory hog — is contained and reported. The engine
refuses rather than ever running without isolation.
