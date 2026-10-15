# How to add a component (any switchable part)

1. **Pick the slot** from `docs/reference/components.md` (e.g. `checkpointer`).
2. **Implement the interface** from `agent_contracts.interfaces`:

   ```python
   from agent_contracts.interfaces import Checkpointer

   class RedisCheckpointer(Checkpointer):
       """Checkpoints in Valkey (dev only)."""
       ...
   ```

3. **Run the shared contract** in your package's tests:

   ```python
   from agent_contracts.testing import CheckpointerContract

   class TestRedisCheckpointer(CheckpointerContract):
       def make(self):
           return RedisCheckpointer(url="redis://127.0.0.1:6379/15")
   ```

4. **Register it** with an entry point in your package's `pyproject.toml`:

   ```toml
   [project.entry-points."agent_platform.components"]
   "checkpointer.valkey" = "agent_memory.valkey:RedisCheckpointer"
   ```

5. **Mark its status** if it is not complete: `component_status = "stub"` plus an ADR saying
   when it will be finished.
6. `uv sync --all-packages`, then `just components --slot checkpointer` shows it and
   `just catalogue` regenerates the catalogue.
7. **Choose it by configuration** (`agent.yaml` or `platform.yaml`, from Step 3) — no
   application code changes.
