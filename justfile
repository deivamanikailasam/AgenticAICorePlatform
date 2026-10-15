# Agentic AI Core Platform — task menu. Run `just` to list recipes.
set dotenv-load := true
set shell := ["bash", "-euo", "pipefail", "-c"]

compose := "docker compose -f infra/compose/compose.yaml"
cerbos_image := "docker.io/cerbos/cerbos:0.56.0@sha256:540643bc67ba628011a150ad1d3370f285daa38995be5cd7a4be181da902bec1"
prometheus_image := "docker.io/prom/prometheus:v3.15.0@sha256:efd719c99d83b060d9daefdcf00360461adf279f45ef5391f8d111892118753e"

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
check: lint typecheck test policy-test obs-test compose-check secret-scan catalogue-check spec-reference-check cards-check validate

lint:
    uv run ruff check .
    uv run ruff format --check .

fmt:
    uv run ruff format .
    uv run ruff check --fix .

typecheck:
    uv run mypy scripts $(for d in libs/*/src services/*/src apps/*/src; do [ -n "$(find "$d" -name '*.py' -print -quit 2>/dev/null)" ] && echo "$d"; done)

test *args:
    uv run pytest {{args}}

# Tests that need Project 0 (or the stand-in stack) running
test-integration *args:
    uv run pytest -m integration {{args}}

# Rego unit tests for every policy bundle
policy-test:
    opa fmt --fail --list policies/ || (echo "policy-test: run 'opa fmt -w policies/'" && exit 1)
    opa check --strict policies/
    opa test policies/ --explain fails
    just policy-test-cerbos

# Prometheus rules (Step 30): syntax check and promtool unit tests (needs Docker)
obs-test:
    docker run --rm --entrypoint promtool -v "$PWD/infra/observability:/obs:ro" -w /obs/prometheus-tests {{prometheus_image}} check rules ../prometheus/agent-rules.yaml
    docker run --rm --entrypoint promtool -v "$PWD/infra/observability:/obs:ro" -w /obs/prometheus-tests {{prometheus_image}} test rules agent-rules.test.yaml
    uv run python -m scripts.check_dashboards

# Compile the Cerbos alternative policies and run their tests (needs Docker)
policy-test-cerbos:
    docker run --rm -v "$PWD/policies/cerbos:/policies:ro" {{cerbos_image}} compile /policies

# Load Project A's Rego policies into OPA (dev/CI; production uses bundles, Step 38)
provision-policies:
    uv run python -m scripts.provision.opa_policies

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
provision: provision-db provision-keycloak provision-litellm provision-artefacts provision-policies provision-openbao provision-grafana

provision-db:
    uv run python -m scripts.provision.agent_db

provision-keycloak:
    uv run python -m scripts.provision.keycloak

provision-litellm:
    uv run python -m scripts.provision.litellm_keys

# OpenFGA store and model (only when relationship checks are used)
provision-openfga:
    uv run python -m scripts.provision.openfga

# Step 29: KV mount, dynamic database credentials, runtime AppRole; imports .tmp/litellm-keys.env
provision-openbao:
    uv run python -m scripts.provision.openbao

# Step 30: dashboards into Project 0's Grafana (folder "Agent platform")
provision-grafana:
    uv run python -m scripts.provision.grafana

provision-artefacts:
    uv run python -m scripts.provision.artefacts

# Start / stop Project A services on Project 0's Docker network
up:
    {{compose}} build
    {{compose}} run --rm agent-health
    {{compose}} up -d --wait --remove-orphans mcp-gateway agent-api temporal agent-worker

# Durable long runs (Step 19): the Temporal development server on 127.0.0.1:7233 (UI on 8233)
up-temporal:
    {{compose}} up -d --wait temporal

# Optional alternative engines (profiles): Cerbos, OpenFGA
up-alternatives:
    {{compose}} --profile cerbos --profile openfga up -d --wait cerbos openfga

down:
    {{compose}} --profile cerbos --profile openfga down

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

# Regenerate docs/reference/components.md from the registry
catalogue:
    uv run python -m scripts.catalogue

catalogue-check:
    uv run python -m scripts.catalogue --check

# Validate agent.yaml specs (files or directories); --strict treats warnings as errors
validate *paths="agents/":
    uv run agent-spec validate {{paths}}

# Regenerate the agent.yaml JSON Schema and reference page in docs/reference/
spec-reference:
    uv run python -m scripts.spec_reference

spec-reference-check:
    uv run python -m scripts.spec_reference --check

# Compile a spec and print its ExecutablePlan
compile spec:
    uv run agent-compile compile {{spec}}

# Run a spec once (traced): just run agents/hello-workflow/agent.yaml '{"name":"Ada"}'
run spec input="{}":
    uv run agent-compile run {{spec}} --input '{{input}}'

# Apply database migrations to the agent database (idempotent)
migrate:
    uv run python -m scripts.migrate

# Agent registry (Step 25): list every agent with its lifecycle status, or change one:
#   just agents --approve NAME VERSION --by you --reason "..." [--expires 2027-01-31]
agents *args:
    uv run python -m agent_registry {{args}}

# Compare the platform's cost ledger with LiteLLM's spend per role key (Step 26)
cost-reconcile *args:
    uv run python -m scripts.cost_reconcile {{args}}

# Verify the audit trail's hash chain (Step 31): the shared Postgres trail, or --file <jsonl>
audit-verify *args:
    uv run python -m scripts.audit_verify {{args}}

# Reconstruct a run from the audit trail plus its checkpoints, with cross-checks (Step 31)
audit-replay run_id *args:
    uv run python -m scripts.audit_replay {{run_id}} {{args}}

# Regenerate tool and agent cards from the registries
cards:
    uv run python -m scripts.cards

cards-check:
    uv run python -m scripts.cards --check

# Run the sandbox runner on the host (needs Docker with the gVisor runtime `runsc`)
sandbox-runner:
    uv run sandbox-runner

# Sandbox tests (need Docker + runsc; skipped where unavailable)
test-sandbox:
    uv run pytest -m sandbox services/sandbox-runner

# Compare agent patterns on the reference tasks with the real local model (Mac; VERIFY-ON-MAC)
compare-patterns cases="evals/reference/planning-tasks/cases.jsonl":
    uv run python -m agent_compiler.compare {{cases}}

# Routing accuracy of the LLM, embedding and rules routers on the labelled set (Mac; VERIFY-ON-MAC)
eval-routing dataset="evals/labelled/routing" routers="llm,embedding,rules":
    uv run python -m agent_compiler.route_eval {{dataset}} {{routers}}

# Agentic RAG vs Project 0's naive RAG on the multi-hop golden set (Mac; VERIFY-ON-MAC)
eval-agentic-rag dataset="evals/golden/agentic-rag":
    uv run python -m agent_compiler.rag_eval {{dataset}}

# Long-term memory on repeated tasks (Step 20): offline by default; flags select store/model
eval-memory *args:
    uv run python -m agent_memory.repeat_eval evals/golden/memory-repeat {{args}}

# Long dialogues within the context window (Step 21): strategies against the unlimited baseline
eval-long-dialogue *args:
    uv run python -m agent_runtime.dialogue_eval evals/golden/long-dialogue {{args}}

# Team patterns (Step 23): reference specs on golden tasks, plus limit checks (stand-in agents)
# Guardrail eval suite (Step 27): labelled attacks, look-alikes and checks; Presidio cases
# run when P0_PRESIDIO_ANALYZER_URL is set
eval-guardrails dataset="evals/golden/guardrails":
    uv run python -m agent_guardrails.eval {{dataset}}

eval-teams:
    uv run python -m agent_compiler.team_eval evals/golden/teams

# The hybrid reference app against Project 0 (Mac; VERIFY-ON-MAC): just demo-invoice-triage INV-1002 j.smith
demo-invoice-triage invoice="INV-1002" approver="":
    uv run python -m invoice_triage.demo {{invoice}} {{ if approver != "" { "--approver " + approver } else { "" } }}

# Firecracker and Kata engines (needs /dev/kvm; see scripts/install_firecracker.sh, install_kata.sh)
test-microvm:
    uv run pytest -m microvm services/sandbox-runner -rs
