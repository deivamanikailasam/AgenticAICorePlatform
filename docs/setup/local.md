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

Expected: six rows, all `OK`: `litellm-gateway`, `postgres`, `valkey`, `keycloak`, `opa`,
`otel-collector`.

## 4. Provision Project A's resources

```bash
just with-secrets just provision
just with-secrets just provision   # second run: every line says "exists"
```

Move the new key values from `.tmp/litellm-keys.env` into OpenBao (automated in Step 29), then
delete the file.

## 5. Start Project A services

```bash
just up
```

At Step 1 this builds the ops image and runs `agent-health` inside Project 0's network.

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

Later steps extend this table (gateway via `host.docker.internal`, Keycloak audience mismatch,
OPA denies everything, Temporal namespace missing, sandbox without gVisor, MCP version
negotiation).
