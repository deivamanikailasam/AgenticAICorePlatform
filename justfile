# Agentic AI Core Platform — task menu. Run `just` to list recipes.
set dotenv-load := true
set shell := ["bash", "-euo", "pipefail", "-c"]

compose := "docker compose -f infra/compose/compose.yaml"

default:
    @just --list

# Install the pinned toolchain and git hooks
bootstrap:
    uv sync --all-packages
    uv run pre-commit install

# Re-sync the virtualenv exactly to uv.lock
sync:
    uv sync --all-packages --frozen

# Everything a contributor runs before pushing (CI runs the same)
check: lint typecheck test policy-test compose-check secret-scan

lint:
    uv run ruff check .
    uv run ruff format --check .

fmt:
    uv run ruff format .
    uv run ruff check --fix .

typecheck:
    uv run mypy scripts $(ls -d libs/*/src services/*/src 2>/dev/null || true)

test *args:
    uv run pytest {{args}}

# Tests that need Project 0 (or the stand-in stack) running
test-integration *args:
    uv run pytest -m integration {{args}}

# Rego unit tests for every policy bundle
policy-test:
    @if compgen -G "policies/**/*.rego" > /dev/null || compgen -G "policies/*.rego" > /dev/null; then opa test policies/ -v --explain fails; else echo "policy-test: no policies yet"; fi

compose-check:
    uv run python -m scripts.check_compose
    @if [ -f infra/compose/compose.yaml ]; then {{compose}} config --quiet && echo "compose config: ok"; fi

secret-scan:
    uv run python -m scripts.check_secrets

# Check the last commit (or a range) carries a docs/changes entry
change-entry range="HEAD~1..HEAD":
    uv run python -m scripts.check_change_entry {{range}}

# Reach every Project 0 service Project A depends on
health:
    uv run python -m scripts.health

# Create Project A's shared resources in Project 0 (idempotent)
provision: provision-db provision-keycloak provision-litellm

provision-db:
    uv run python -m scripts.provision.agent_db

provision-keycloak:
    uv run python -m scripts.provision.keycloak

provision-litellm:
    uv run python -m scripts.provision.litellm_keys

# Start / stop Project A services on Project 0's Docker network
up:
    {{compose}} build
    {{compose}} run --rm agent-health

down:
    {{compose}} down

logs *svc:
    {{compose}} logs -f {{svc}}

# Stand-in for Project 0's services, for CI and cloud sessions only (never on the Mac)
standin-up:
    docker compose -f tests/stack/compose.p0-standin.yaml up -d --wait

standin-down:
    docker compose -f tests/stack/compose.p0-standin.yaml down -v

# New architecture decision record from the template
adr title:
    uv run python -m scripts.new_adr "{{title}}"

# Decrypt dev secrets into the environment of a command: just with-secrets env
with-secrets *cmd:
    sops exec-env secrets/dev.enc.env '{{cmd}}'

# Encrypt secrets/dev.env into secrets/dev.enc.env with Project 0's age key
secrets-init:
    ./scripts/secrets_init.sh

# List every registered component (the switchable parts)
components *args:
    uv run agent-components {{args}}
