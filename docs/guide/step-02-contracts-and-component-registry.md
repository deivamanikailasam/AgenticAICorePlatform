# Step 2 · Contracts and the component registry

## 1. At a glance

| | |
|---|---|
| **Goal** | A typed "socket" for every switchable part, and a registry that finds plugs by name |
| **Time** | About a day |
| **You end up with** | `libs/agent-contracts`: 23 interfaces, the shared data models, a registry with entry-point discovery, a contract test per interface, 46 dummy implementations, `just components`, a generated catalogue |
| **New words** | interface, contract test, registry, entry point, slot, dummy, test double |

## 2. The big idea

Think of a house wired with standard sockets. Lamps, kettles and chargers all have the same
plug, so you can swap one for another without calling an electrician. Step 2 defines the
sockets of the platform (interfaces), a directory of what is plugged in where (the registry),
and a socket tester that every plug must pass (contract tests).

> *Analogy:* a contract test is the electrical safety check a new appliance must pass before it
> is sold. Whoever made the kettle, it has to pass the same check.

## 3. What we're doing

1. Define the **shared models** every part exchanges (`agent_contracts.models`): messages with a
   trust label, tool specs and results, plans, run events and states, checkpoints, memory items,
   guardrail results, policy decisions, approvals, budgets, audit records, eval scores.
2. Define **23 interfaces** (`agent_contracts.interfaces`), one per slot from the spec.
3. Build the **registry** (`agent_contracts.registry`): `@register`, entry-point discovery,
   lookup by slot and name, a CLI (`just components`).
4. Write **one contract test class per interface** (`agent_contracts.testing`).
5. Register **two dummy implementations per interface** and run every contract against them.
6. **Generate the catalogue** (`docs/reference/components.md`) and fail CI when it is stale.

## 4. Why we're doing this

Without contracts, every pattern would talk directly to LangGraph, OPA or Postgres, and
replacing any of them would mean rewriting the patterns. With contracts, a team can swap the
runtime, the policy engine or the memory store by changing one name in configuration, and the
contract test tells them immediately whether the replacement behaves correctly. Enterprises
care because vendors and frameworks change faster than business processes.

> *Why this matters:* the safety properties of the platform live in the contracts. For example,
> "a tool executor never runs a tool with schema-invalid arguments" and "a policy decision point
> never plainly allows an irreversible action" are contract tests, so *every* implementation,
> today's and tomorrow's, must keep them.

## 5. Key ideas

- **Interface.** A list of methods with their inputs and outputs, and no implementation. Python
  abstract base classes make forgetting a method an error at construction time.
- **Slot.** The registry's name for an interface (`tool_executor`).
- **Registry.** A directory: `(slot, name) → class`. Configuration names a component; the
  registry finds it.
- **Entry point.** A standard Python packaging feature: a package declares "I provide
  `tool_executor.mcp`, found at `agent_tools.mcp:McpToolExecutor`". Installing the package makes
  it discoverable, with no central list to edit.
- **Contract test.** A test written once against the interface and run against every
  implementation.
- **Dummy / test double.** A deliberately simple implementation used to prove a contract can be
  met and to make other tests fast. Labelled `dummy` in the catalogue.
- **Trust label.** Every message and tool result carries `trusted`, `user` or `untrusted`.
  Untrusted content is data, never instructions — design principle 6, built into the types.

## 6. How to do it

```bash
just bootstrap                     # installs libs/agent-contracts into the workspace
just components                    # 46 components across 23 of 23 slots
just components --slot guardrail   # just one slot
uv run pytest libs/agent-contracts # every contract against every dummy
just catalogue                     # regenerate docs/reference/components.md
just check                         # includes catalogue-check
```

Key files:

| File | Purpose |
|---|---|
| `libs/agent-contracts/src/agent_contracts/models.py` | Shared Pydantic models |
| `libs/agent-contracts/src/agent_contracts/interfaces.py` | The 23 interfaces and `INTERFACES` map |
| `libs/agent-contracts/src/agent_contracts/registry.py` | Registry, discovery, CLI |
| `libs/agent-contracts/src/agent_contracts/testing/contracts.py` | Contract test classes |
| `libs/agent-contracts/src/agent_contracts/dummies/` | Two dummies per interface |
| `libs/agent-contracts/tests/test_dummies_pass_contracts.py` | Generates a contract run per dummy |
| `scripts/catalogue.py` | Writes and checks the catalogue |
| `docs/how-to/add-a-component.md` | The recipe for adding a component |

The 23 slots: `model_client`, `tool`, `tool_registry`, `tool_executor`, `pattern_builder`,
`runtime_adapter`, `checkpointer`, `durable_executor`, `memory_store`, `memory_policy`,
`context_manager`, `guardrail`, `policy_decision_point`, `approval_gate`, `budget_controller`,
`loop_detector`, `planner`, `router`, `critic`, `telemetry_emitter`, `audit_sink`,
`evaluator`, `simulator`.

## 7. Benefits

- Swapping a component is a configuration change, checked by the same tests.
- Safety rules are enforced for every implementation, not just the first one.
- Fast, deterministic dummies make the rest of the test suite quick (Step 33's 5-minute goal).
- The catalogue is always accurate because it is generated.

## 8. Drawbacks and risks

- Interfaces are expensive to change later; a mistake here ripples everywhere.
- Lowest-common-denominator risk: an engine feature that not every adapter supports cannot be
  in the interface.
- Dummies can be mistaken for real components; the `dummy` status label guards against that.

## 9. Alternatives

| Choice | Alternative | When the alternative wins |
|---|---|---|
| Abstract base classes | `typing.Protocol` | When you can't make implementations inherit (third-party classes) |
| Entry points | pluggy, stevedore | When you need hook ordering or plugin lifecycles |
| Pydantic v2 models | dataclasses, attrs | When validation and JSON Schema are not needed |
| Contract tests | Per-implementation tests only | Never for a safety property |

## 10. Which approach fits which situation

- **Adding an implementation of an existing slot:** follow `docs/how-to/add-a-component.md`.
- **A new kind of component:** it needs a new interface, a contract and an ADR; first check
  whether an existing slot fits.
- **Unit-testing a pattern:** use the dummies (`model_client.scripted`, `runtime_adapter.sequential`)
  rather than mocks.

## 11. Common mistakes

- Registering a component before its contract test passes.
- Treating a tool result as instructions: tool results are `Trust.UNTRUSTED` by default.
- Editing `docs/reference/components.md` by hand (CI will fail).
- Using the same entry-point name in two packages: the registry refuses the second class.

## 12. Done when

| Check | Status | Evidence |
|---|---|---|
| Two dummy implementations of each interface pass the contract tests | ✅ | `uv run pytest libs/agent-contracts`: 184 passed, 4 skipped (the 4 are interface-type checks for components built with collaborators; their behaviour tests ran) |
| `just components` lists them | ✅ | `46 components across 23 of 23 slots` |
| Catalogue regenerated | ✅ | `just catalogue-check`: up to date |
| ADR for hard-to-reverse choice | ✅ | ADR 0012 |
| Traces, metrics and audit events for new behaviour | n/a | Step 2 adds no runtime behaviour; telemetry and audit interfaces are defined here |
| Evals | n/a | No agent behaviour yet |

## 13. Recap

Step 2 created the platform's standard sockets: 23 typed interfaces and the data they exchange,
a registry that finds implementations by slot and name through Python entry points, one
contract test per interface that encodes the behaviour (and safety rules) every implementation
must keep, two dummy implementations of each to prove it, and a generated catalogue that can
never drift from what is installed.
