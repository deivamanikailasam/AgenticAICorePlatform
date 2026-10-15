"""Durable runs (Step 18): a checkpoint after every node, and resuming from the last one.

``CheckpointingNodeRunner`` writes a checkpoint (tenant, thread, run, step, node, outcome, value)
after every node and every approval decision. After a crash, ``load_journal`` rebuilds the run's
journal from those checkpoints and a runner built with ``replay=journal, resume=True`` answers
completed nodes from the journal (no model call, no tool call, so no repeated write) and runs the
rest live. Tool nodes keep their stable idempotency key ``<run>:<node>``, so even a write that
was in flight when the worker died is not repeated by the platform executor.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from agent_contracts.interfaces import Checkpointer
from agent_contracts.models import Checkpoint, PlanNode, RunContext

from agent_runtime.nodes import NodeExecutor, NodeFn, NodeOutcome, NodeRunner, RunJournal


class CheckpointingNodeRunner(NodeRunner):
    def __init__(
        self,
        functions: Mapping[str, NodeFn] | None = None,
        executors: Mapping[Any, NodeExecutor] | None = None,
        replay: RunJournal | None = None,
        resume: bool = False,
        *,
        checkpointer: Checkpointer,
    ) -> None:
        super().__init__(functions, executors, replay, resume)
        self.checkpointer = checkpointer
        self._last: dict[str, str] = {}
        self._steps: dict[str, int] = {}
        self._pending: set[asyncio.Task[None]] = set()
        if replay is not None:
            self._steps[replay.run_id] = sum(len(v) for v in replay.entries.values())

    async def _save(self, ctx: RunContext, node_id: str, result: NodeOutcome, kind: str) -> None:
        step = self._steps.get(ctx.run_id, 0)
        self._steps[ctx.run_id] = step + 1
        cp = Checkpoint(
            tenant_id=ctx.tenant_id,
            thread_id=ctx.thread_id,
            run_id=ctx.run_id,
            step=step,
            node_id=node_id,
            parent_id=self._last.get(ctx.run_id),
            state={
                "kind": kind,
                "outcome": result.outcome,
                "value": result.value,
                "error": result.error,
            },
        )
        self._last[ctx.run_id] = cp.checkpoint_id
        await self.checkpointer.put(cp)

    async def flush(self) -> None:
        """Wait for checkpoints of approval decisions (recorded synchronously by adapters)."""
        while self._pending:
            await asyncio.gather(*list(self._pending))

    async def run(self, node: PlanNode, values: dict[str, Any], ctx: RunContext) -> NodeOutcome:
        await self.flush()
        replayed = self.replays(node.id)
        result = await super().run(node, values, ctx)
        if not replayed:
            await self._save(ctx, node.id, result, "node")
        return result

    def record(self, ctx: RunContext, node_id: str, result: NodeOutcome) -> NodeOutcome:
        recorded = super().record(ctx, node_id, result)
        task = asyncio.get_running_loop().create_task(self._save(ctx, node_id, result, "approval"))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        return recorded


async def load_journal(cp: Checkpointer, ctx: RunContext) -> RunJournal | None:
    """The journal of ``ctx.run_id`` rebuilt from its checkpoints, or None if it has none."""
    rows = [c for c in await cp.list(ctx.tenant_id, ctx.thread_id) if c.run_id == ctx.run_id]
    if not rows:
        return None
    journal = RunJournal(ctx.run_id)
    for c in sorted(rows, key=lambda c: c.step):
        if c.node_id is None or c.state.get("kind") not in ("node", "approval"):
            continue
        journal.record(
            c.node_id,
            NodeOutcome(str(c.state["outcome"]), c.state.get("value"), c.state.get("error")),
        )
    return journal
