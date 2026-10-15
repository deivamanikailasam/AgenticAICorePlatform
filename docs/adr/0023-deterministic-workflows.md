# ADR 0023: Deterministic workflows — platform-level saga, journal replay, value paths, stable idempotency keys

- **Status:** Accepted (point 5 amended by ADR 0026: approval evidence reaches policy from Step 15)
- **Date:** 2026-10-15
- **Step:** 12
- **Deciders:** repository owner

## Context
Step 12 needs function, LLM and tool nodes, conditional edges, parallel branches, joins,
sub-workflows, timeouts and saga compensation, with "replay produces the same path". These must
behave identically on every runtime adapter (ADR 0022). No new third-party dependency was added
in this step.

## Decision
1. **Saga in the platform, not the engine.** Compensation is declared per node in the spec
   (`compensation: <node>`); on a failed run, finished steps with a compensation are undone newest
   first by the shared node module. Engines differ in how (or whether) they support sagas;
   keeping it in one place gives the same behaviour everywhere. Compensation nodes take no edges
   and are never starts, ends or "unreachable".
2. **Replay from a journal, not from engine history.** Every node result is journalled per run;
   a replay runner returns recorded results instead of executing, including approvals. This
   gives the same path on any engine, needs no model or tool access, and detects divergence.
   (LangGraph's own checkpoint "time travel" remains available for debugging; it is
   engine-specific.)
3. **Values by path, never evaluated.** Arguments and prompts refer to run values as
   `$input.x` / `${node.field}`; lookup walks dicts and lists only. Templates use
   `string.Template` substitution (no format specs, no attribute access), so model or tool output
   inserted into a later prompt or argument can never execute.
4. **Stable idempotency keys.** Tool nodes use `<run id>:<node id>`; with Step 6's idempotency
   store a retry, resume or replay of the same run cannot repeat a side effect.
5. **Approvals:** the spec's `approval` is enforced by the compiler-inserted APPROVAL node, so the
   tool grant does not ask again. Approvals required by *policy* still stop tool nodes until
   Step 28 supplies approval evidence to policy — fail closed.
6. **Routers** can only return a declared route (enforced by schema for model routing and by a
   check for both kinds).
7. **Composition root:** `agent_compiler.run` plugs executors into the runner; the compiler package
   now depends on `agent-runtime` and `agent-tools` (no cycle: neither depends on the compiler).
8. **Prompt registry refs** (`prompt://…`) are refused with a clear message until the registry
   exists (Project 0 format, Step 24); inline `config.template` is used until then.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| Engine-native compensation (e.g. Temporal sagas) | Engine-specific; Temporal arrives in Step 19 and will reuse the same declarations |
| Jinja2 templates | Powerful but a code surface; sandboxing it is harder than not needing it |
| Replay via LangGraph checkpoints only | Ties replay to one engine |
| Random idempotency keys per attempt | Retries could repeat side effects |

## Consequences
Compensation is best effort: a failing undo leaves the run failed with the failure visible.
Journals and run state are in memory until Step 18.

## Verification
`libs/agent-compiler/tests/test_deterministic_workflows.py` (18 tests, both adapters),
`libs/agent-runtime/tests/test_saga_and_replay.py`, `test_executors.py`, `test_assemble.py`.
