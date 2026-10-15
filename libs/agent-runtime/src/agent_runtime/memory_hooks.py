"""How a run uses long-term memory (Step 20), without the runtime depending on a memory store.

The agent loop calls a ``RunMemory`` (implemented by ``agent_memory.service.MemoryService``):

* ``recall`` before the first model call: relevant memories become messages, each marked with
  the trust of where it came from, so untrusted memories reach the model delimited as data;
* ``episode`` after the final answer: a record of the run for episodic memory.

``tainted`` says whether the run's context has held untrusted content (tool results, retrieved
documents, untrusted memories). Writes made while it is set are treated as untrusted, so a model
steered by a poisoned document cannot store what the document told it to (OWASP ASI06).
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Protocol, runtime_checkable

from agent_contracts.models import Message, RunContext

tainted: ContextVar[bool] = ContextVar("agent_memory_tainted", default=False)


@runtime_checkable
class RunMemory(Protocol):
    async def recall(self, ctx: RunContext, query: str) -> list[Message]: ...

    async def episode(
        self, ctx: RunContext, task: str, answer: str | None, steps: int, untrusted: bool
    ) -> None: ...
