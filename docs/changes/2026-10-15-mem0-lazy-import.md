# Mem0 is imported only when the Mem0 store is used

**Step:** 20 · **Commit:** fix(agent-memory): import Mem0 only when its store is built · **Date:** 2026-10-15

## What changed
`agent_memory.mem0` now imports the Mem0 library inside the store's constructor, not at module
import. Before importing, it points `MEM0_DIR` at a temporary folder and switches Mem0's
telemetry off. The embedding adapter is duck-typed, so the module no longer subclasses a Mem0
class.

## Why
The component registry loads every entry point at start-up. Importing Mem0 creates `~/.mem0`,
which failed on the services' read-only file systems: `agent-api` would not start. Services that
never use Mem0 should not load it or have it write files.

## How to check it
- Run `uv run python -c "import sys; from agent_contracts.registry import registry; registry.names('memory_store'); print('mem0' in sys.modules)"`.
  It prints `False`.
- `just up`: `agent-api` is healthy.
- The Mem0 contract tests still pass.

## Risks and follow-ups
None known.
