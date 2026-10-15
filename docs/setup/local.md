# Local setup (MacBook Pro with Project 0)

This guide takes a teammate from a fresh Mac with Project 0 running to a green `just smoke`.
It grows with each step; sections marked *(from Step N)* apply once that step has landed.

## 0. What you need first

| Need | Check |
|---|---|
| Project 0 running and healthy | `cd ~/dev/rag-platform && just up && just check` |
| Homebrew | `brew --version` |
| uv 0.11+ | `uv --version` |
| just 1.40+ | `just --version` |
| SOPS 3.13+ and age 1.3+ | `sops --version`, `age --version` |
| OPA 1.21+ | `opa version` |
| Docker via OrbStack | `docker info` |
| Project 0's age key | `ls ~/.config/sops/age/keys.txt` |

Install anything missing (record additions in the Brewfile first):

```bash
brew install uv just sops age opa
```

Later steps add: `temporal` CLI (Step 19), `copier` (Step 39), `promptfoo` (Step 35).

## 1. Clone and sync

```bash
git clone git@github.com:deivamanikailasam/AgenticAICorePlatform.git ~/dev/agent-platform
cd ~/dev/agent-platform
just bootstrap
cp .env.example .env
```

Open `.env` and compare every value with Project 0's compose file
(`~/dev/rag-platform/infra/compose/`): host ports, the Docker network name (`P0_NETWORK`)
and the Keycloak realm (`P0_KEYCLOAK_REALM`). Also compare the service names used inside the
network in `infra/compose/compose.yaml` (`litellm`, `postgres`, `valkey`, `keycloak`, `opa`,
`otel-collector`).

## 2. Dev secrets

```bash
export SOPS_AGE_RECIPIENTS="$(age-keygen -y ~/.config/sops/age/keys.txt)"
cp secrets/dev.env.example secrets/dev.env
$EDITOR secrets/dev.env          # admin DSN, agent DB password, Keycloak admin, LiteLLM master key
just secrets-init                # -> secrets/dev.enc.env (commit this one only)
```

Every later command that needs secrets runs as `just with-secrets <command>`.

## 3. Reach Project 0

```bash
just with-secrets just health
```

Expected: seven rows, all `OK`: `litellm-gateway`, `postgres`, `valkey`, `keycloak`, `opa`,
`otel-collector`, `openbao`.

## 4. Provision Project A's resources

```bash
just with-secrets just provision
just with-secrets just provision   # second run: every line says "exists"
```

The last provisioning step, `provision-openbao`, moves the new LiteLLM key values from
`.tmp/litellm-keys.env` into OpenBao (`agent-platform/litellm/<role>`) and deletes the file once
every key reads back. It also writes the runtime's AppRole to `.tmp/openbao-approle.env`: move
`AGENT_BAO_ROLE_ID` and `AGENT_BAO_SECRET_ID` into `secrets/dev.env`, run `just secrets-init`,
then delete the file. `P0_OPENBAO_URL` defaults to `http://127.0.0.1:8200`; if Project 0's
OpenBao reaches Postgres under a name other than `postgres:5432`, set `AGENT_BAO_DB_HOST`.

Then apply the database migrations (tables for the tool registry and, later, checkpoints,
memory and the agent registry):

```bash
just with-secrets just migrate     # second run: every line says "already applied"
```

`secrets/dev.env` also needs `AGENT_DB_DSN` (the `agent_app` role created by provisioning),
`P0_S3_ACCESS_KEY` and `P0_S3_SECRET_KEY` (Project 0's SeaweedFS S3 credentials).

## 5. Start Project A services

```bash
just up
```

At Step 1 this builds the ops image and runs `agent-health` inside Project 0's network.

### Optional: other policy engines (Step 10)

OPA comes from Project 0 and is the default. To try Cerbos or OpenFGA relationship checks, add
`OPENFGA_PRESHARED_KEY_DEV` and `SECRET__OPENFGA__AGENT_PLATFORM__PRESHARED_KEY` (same value) to
`secrets/dev.env`, then:

```bash
just with-secrets just up-alternatives      # cerbos (127.0.0.1:3592), openfga (127.0.0.1:8085)
just with-secrets just provision-openfga    # second run: "model: up to date"
MCP_GATEWAY_PDP=cerbos+openfga just with-secrets just up
```

OpenFGA here keeps relationships in memory: they are lost when the container restarts.

### Optional: microVM sandbox engines on the Mac (Firecracker, Kata)

gVisor is the default sandbox and needs no KVM. To run the Firecracker or Kata engines, macOS
has no KVM of its own, but an Apple M3-or-later Mac on macOS 15+ (your M5 Max qualifies) can
give a Linux VM nested virtualisation. With [Lima](https://github.com/lima-vm/lima)
(VERIFY-ON-MAC — the flags below are Lima's documented options; confirm with `limactl --version`):

```bash
brew install lima
limactl start --name=agent-sandbox --vm-type=vz --cpus=8 --memory=24 \
  --set '.nestedVirtualization = true' template://default
limactl shell agent-sandbox -- ls -l /dev/kvm          # must exist
limactl shell agent-sandbox                            # then, inside the VM, in the repo:
sudo ./scripts/install_firecracker.sh                  # aarch64 binary + kernel, SHA-256 checked
sudo ./scripts/build_sandbox_rootfs.sh                 # needs Docker inside the VM
sudo ./scripts/install_kata.sh && sudo systemctl restart docker
AGENT_REQUIRE_ENGINES=firecracker,kata just test-microvm
```

24 GB of the 128 GB is plenty for many concurrent sandboxes next to Project 0's local models;
size it down if the models need the memory.

## 6. Verify

```bash
just check
just test-integration
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `network rag-platform declared as external, but could not be found` | Project 0 not running, or a different network name | `docker network ls`; set `P0_NETWORK` in `.env` |
| `litellm-gateway FAIL ConnectError` | Gateway port differs | Check Project 0's compose; update `P0_LITELLM_URL` |
| `keycloak FAIL HTTPStatusError 404` | Realm name differs | Set `P0_KEYCLOAK_REALM` |
| `postgres FAIL password authentication failed` | DSN without the right credentials | Put the DSN with credentials in `secrets/dev.env`, re-run `just secrets-init` |
| Port already in use on 5432/6379/8080 | The CI stand-in was started on the Mac | `just standin-down` (never use the stand-in on the Mac) |
| `sops: no matching creation rules` | File not under `secrets/*.enc.*` | Use `just secrets-init` |
| Build fails with certificate errors | Corporate proxy | Set `EXTRA_CA_BUNDLE=/path/to/ca.pem` before `just up` |
| Every tool call says `policy agent/tools/decision is not loaded` | Policies not in OPA (OPA restarted) | `just with-secrets just provision-policies` |
| Calls denied with `no rule allows this call` | `data.agent.config` missing in OPA | Same: provisioning loads `policies/agent/config/data.json` |
| `rate_limited` errors | Per-minute limit reached | Wait for the next minute, or raise `rate_limit_per_minute` / `MCP_GATEWAY_RATE_LIMIT_PER_MINUTE` |
| `refusing to call it with a service identity` | `on_behalf_of` MCP server called without a user token | Call through the gateway or agent API as a signed-in user |
| `openfga` exits: `please specify at least one key` | `OPENFGA_PRESHARED_KEY_DEV` not set | Add it to `secrets/dev.env`; run through `just with-secrets` |

Later steps extend this table (gateway via `host.docker.internal`, Keycloak audience mismatch,
Temporal namespace missing, sandbox without gVisor, MCP version negotiation).
