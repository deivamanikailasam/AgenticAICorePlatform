# ADR 0012: Component registry via Python entry points, guarded by shared contract tests

- **Status:** Accepted
- **Date:** 2026-10-15
- **Step:** 02
- **Deciders:** repository owner

## Context
Principle 4 requires every component to be switchable by configuration. We need a way for
packages to offer implementations, for configuration to pick one by name, and for the platform
to trust that any registered implementation behaves correctly.

## Decision
- 23 interfaces are abstract base classes in `agent_contracts.interfaces`, one per slot.
- Implementations register with `@register("<slot>", "<name>")` or, preferably, an entry point
  in group `agent_platform.components` named `<slot>.<name>`. Discovery uses
  `importlib.metadata.entry_points` (standard library, Python 3.12).
- The registry rejects an unknown slot, a class that does not implement the slot's interface,
  and a second, different class under an existing name.
- Each interface has a contract test class in `agent_contracts.testing`. An implementation is
  registered only after its test module subclasses the contract and passes.
- Each component declares `component_status`: `implemented`, `stub` or `dummy`.
  `docs/reference/components.md` is generated from the registry and checked in CI.

## Alternatives considered
| Option | Why not (or when it would win) |
|---|---|
| `typing.Protocol` (structural typing) | No runtime registration check, and no shared base for status metadata |
| A plugin framework (pluggy, stevedore) | Extra dependency for what entry points already provide |
| A YAML list of import paths | Drifts from installed packages; no type check at registration |
| Dependency-injection container | Heavier; configuration-by-name is all we need |

## Consequences
Adding an implementation is: implement the interface, subclass its contract test, add one entry
point line. Interface changes ripple to every implementation and its contract, so they need an
ADR. Pydantic v2 (2.14.x) and jsonschema (4.26.x) are now runtime dependencies of
`agent-contracts`.

## Verification
`just components` lists 46 dummy components across 23 of 23 slots; their contract runs pass
(184 tests at this commit).
