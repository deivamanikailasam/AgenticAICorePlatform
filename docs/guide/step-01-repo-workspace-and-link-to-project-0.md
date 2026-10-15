# Step 1 · Repo, workspace and link to Project 0

## 1. At a glance

| | |
|---|---|
| **Goal** | A clean, checked repository that is connected to Project 0's shared services |
| **Time** | About half a day |
| **You end up with** | A uv workspace, a `justfile` menu, guard scripts, SOPS-encrypted dev secrets, ADRs 0001–0011, a compose file on Project 0's network, idempotent provisioning, a health check and CI |
| **New words** | workspace, lock file, idempotent, external network, virtual key, ADR, stand-in |

## 2. The big idea

Project 0 is a building that already has electricity, plumbing, a security desk and CCTV.
Step 1 rents a room in it for Project A: we get a key card (a Keycloak client), our own storage
cupboard (an `agent` database), our own lines on the shared phone bill (LiteLLM keys per agent
role), and we test that every socket in the room works (the health check).

> *Analogy:* moving into a serviced office. You don't install your own power plant; you check
> that the sockets, the network and the badge reader work, and you label your cupboard.

## 3. What we're doing

1. Create a Python 3.12 **uv workspace** with one lock file and pinned dev tools.
2. Add a **`justfile`** with the commands everyone uses: `check`, `health`, `provision`, `up`.
3. Add **guard scripts** that fail the build on unsafe configuration or leaked secrets.
4. Configure **SOPS** so dev secrets are committed only in encrypted form.
5. Write the **founding ADRs** (0001–0010 from the spec, 0011 for the cloud build).
6. Add **`infra/compose/compose.yaml`**, which joins Project 0's Docker network.
7. Add **idempotent provisioning** for the `agent` database, the Keycloak client and LiteLLM keys.
8. Add a **health script** that reaches the gateway, Postgres, Valkey, Keycloak, OPA and the
   OTel Collector.
9. Add **CI** that runs the same checks on every push.

## 4. Why we're doing this

Every later step needs these shared services. If the connection is shaky, every failure later
looks like an agent bug when it is really a wiring problem. Enterprises care because the same
scripts that set up a laptop set up staging and production: no hand-made, undocumented setup.

> *Why this matters:* "works on my machine" is the most expensive sentence in software. A lock
> file, idempotent scripts and CI are how a team makes "works" mean the same thing everywhere.

## 5. Key ideas

- **Workspace and lock file.** A workspace holds several packages (`libs/*`, `services/*`)
  that are developed together. `uv.lock` records the exact version of every dependency, so
  `uv sync --frozen` gives everyone identical installs.
- **Idempotent.** An operation is idempotent if doing it twice has the same effect as doing it
  once. Provisioning checks "does it exist?" before creating anything.
- **External network.** Docker containers talk over networks. `external: true` means "this
  network belongs to someone else (Project 0); join it, don't create it".
- **Virtual key.** A LiteLLM key that is not a real provider key. It carries limits (which
  models, how much money) and lets the gateway attribute cost to one agent role.
- **ADR.** A one-page record of a hard-to-reverse decision and the options rejected.
- **Stand-in stack.** The same upstream images Project 0 runs, started only in CI or a cloud
  session to test integration when Project 0 itself is not reachable (ADR 0011).

## 6. How to do it

On the Mac (Project 0 running):

```bash
cd ~/dev/agent-platform
just bootstrap                         # uv sync + git hooks
cp .env.example .env                   # check every address against Project 0's compose file
export SOPS_AGE_RECIPIENTS="$(age-keygen -y ~/.config/sops/age/keys.txt)"
cp secrets/dev.env.example secrets/dev.env && $EDITOR secrets/dev.env
just secrets-init                      # writes secrets/dev.enc.env, removes the plaintext
just with-secrets just health          # six OK rows
just with-secrets just provision       # first run: "created"; second run: "exists"
just up                                # health check from inside Project 0's network
just check                             # all green
```

In a cloud session or CI (no Project 0):

```bash
just standin-up
set -a; . tests/stack/standin.env; set +a
just health && just provision && just up && just test-integration
just standin-down
```

Files added in this step:

| File | Purpose |
|---|---|
| `pyproject.toml`, `uv.lock` | Workspace definition and exact versions |
| `justfile` | Task menu |
| `.pre-commit-config.yaml` | Local hooks running the same tools as `just check` |
| `scripts/check_compose.py` | Ports on 127.0.0.1, digests, memory limits, no privileged/socket mounts |
| `scripts/check_secrets.py` | Pattern-based secret scan; `*.enc.*` must be encrypted |
| `scripts/check_change_entry.py` | Every commit has a `docs/changes/` entry |
| `scripts/health.py` | Reaches the six Project 0 dependencies |
| `scripts/provision/*.py` | Idempotent DB, Keycloak and LiteLLM provisioning |
| `.sops.yaml`, `secrets/dev.env.example`, `scripts/secrets_init.sh` | Encrypted dev secrets |
| `infra/compose/compose.yaml`, `infra/compose/ops.Dockerfile` | Project A containers on Project 0's network |
| `tests/stack/` | Project 0 stand-in for CI only |
| `docs/adr/0001…0011` | Founding decisions |
| `.github/workflows/ci.yml` | CI |

## 7. Benefits

- One command (`just check`) tells you whether the repo is healthy.
- Unsafe container settings and committed secrets are caught before they are pushed.
- Provisioning is repeatable across laptops and environments.
- Decisions are written down with their alternatives.

## 8. Drawbacks and risks

- The stand-in is not Project 0: realm names, service names and network names are assumptions
  until checked on the Mac.
- Pattern-based secret scanning can miss secrets in unusual formats.
- Budgets in `litellm-keys.yaml` are guard limits chosen before any measurement.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| uv | Poetry, pip-tools | A team already standardised on them |
| just | make, Taskfile | `make` is everywhere; `just` has nicer syntax and no tab traps |
| SOPS + age | git-crypt, a dev-only Vault | Vault when developers already have short-lived access to it |
| Compose `external` network | A separate network with published ports | When Project A runs on a different host from Project 0 |
| Pattern secret scan | gitleaks, trufflehog | Larger repos; add them in CI (Step 38) |

## 10. Which approach fits which situation

- **Solo laptop with Project 0:** follow section 6 "On the Mac".
- **Cloud session or CI:** use the stand-in, and record every check that still needs the Mac.
- **Shared staging cluster:** the same provisioning scripts run as Kubernetes Jobs (Step 38).

## 11. Common mistakes

- Starting the stand-in on the Mac while Project 0 is running: both want ports 5432, 6379, 8080.
- Forgetting `just with-secrets` and wondering why provisioning says "set …".
- Committing `secrets/dev.env` (the plaintext). `.gitignore` blocks it; `secret-scan` checks too.
- Editing an accepted ADR instead of superseding it.

## 12. Done when

| Check | Status | Evidence |
|---|---|---|
| `just check` passes | ✅ verified in container | ruff clean, mypy clean, 12 passed, 0 findings |
| `just up` starts Project A services against a running Project 0 | ⚠️ verified against stand-in; **VERIFY-ON-MAC** | six OK rows from inside the network |
| Health script reaches gateway, Postgres, Valkey, Keycloak, OPA, OTel Collector | ⚠️ stand-in verified; **VERIFY-ON-MAC** | `just health` output in `docs/changes/2026-10-15-compose-and-standin.md` |
| `agent` DB, Keycloak client, LiteLLM keys created by idempotent scripts | ⚠️ stand-in verified; **VERIFY-ON-MAC** | second `just provision` run prints only "exists" |
| ADRs 0001–0010 | ✅ | `docs/adr/` |
| CI green | see PROGRESS.md | GitHub Actions |

## 13. Recap

Step 1 turned an empty folder into a checked, connected workspace: pinned tools, a task menu,
guard scripts for ports, images and secrets, encrypted dev secrets, written decisions, a compose
file that plugs into Project 0's network, idempotent provisioning of Project A's database,
identity and model keys, a health check for every dependency, and CI that runs it all on every
push.
