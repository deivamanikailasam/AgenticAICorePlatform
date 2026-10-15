"""Node semantics shared by every runtime adapter (Step 11).

An adapter decides *when* nodes run (order, parallelism, interrupts, checkpoints); this module
decides *what* a node does and *where* the run goes next, so the same plan behaves the same way on
every engine:

* a FUNCTION node calls its function with the run's values (``input`` plus every finished node's
  value by id); the outcome is ``ok``, ``error`` if it raised or timed out, or the ``_outcome``
  key the function returns;
* JOIN nodes pass through (``ok``); APPROVAL nodes are interrupts, handled by the adapter;
* LLM, TOOL, AGENT, ROUTER and SUBPLAN nodes run through executors the platform plugs in
  (Steps 12 and 13); without one the node fails with a clear error;
* an edge is taken when its condition is empty, the outcome, or ``on_<outcome>``; an ``error``
  outcome with no matching edge fails the run.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from agent_contracts.models import ExecutablePlan, NodeKind, PlanNode, RunContext

NodeFn = Callable[[dict[str, Any]], Any | Awaitable[Any]]
#: Runs a non-function node: (node, values, ctx) -> value; raise to fail; return
#: {"_outcome": ...} to choose a branch.
NodeExecutor = Callable[[PlanNode, dict[str, Any], RunContext], Awaitable[Any]]


@dataclass(frozen=True)
class NodeOutcome:
    outcome: str
    value: Any
    error: str | None = None


def approval_outcome(payload: Mapping[str, Any]) -> NodeOutcome:
    return NodeOutcome("ok" if payload.get("approved") else "rejected", dict(payload))


class NodeRunner:
    def __init__(
        self,
        functions: Mapping[str, NodeFn] | None = None,
        executors: Mapping[NodeKind, NodeExecutor] | None = None,
    ) -> None:
        self.functions = dict(functions or {})
        self.executors = dict(executors or {})

    async def _call(self, node: PlanNode, values: dict[str, Any], ctx: RunContext) -> Any:
        if node.kind is NodeKind.FUNCTION:
            name = str(node.config["fn"])
            if name not in self.functions:
                raise LookupError(f"function '{name}' is not registered")
            result = self.functions[name](values)
            return await result if inspect.isawaitable(result) else result
        if node.kind is NodeKind.JOIN:
            return None
        executor = self.executors.get(node.kind)
        if executor is None:
            raise NotImplementedError(f"no executor for {node.kind.value} nodes")
        return await executor(node, values, ctx)

    async def run(self, node: PlanNode, values: dict[str, Any], ctx: RunContext) -> NodeOutcome:
        timeout = node.config.get("timeout_seconds")
        try:
            call = self._call(node, values, ctx)
            result = await (asyncio.wait_for(call, float(timeout)) if timeout else call)
        except TimeoutError:
            return NodeOutcome("error", {"error": f"timed out after {timeout}s"}, "timeout")
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            return NodeOutcome("error", {"error": message}, message)
        if isinstance(result, dict) and "_outcome" in result:
            return NodeOutcome(str(result["_outcome"]), result)
        return NodeOutcome("ok", result)


def next_targets(plan: ExecutablePlan, node_id: str, outcome: str) -> list[str]:
    return [
        e.target
        for e in plan.edges
        if e.source == node_id and e.condition in (None, outcome, f"on_{outcome}")
    ]


def run_output(plan: ExecutablePlan, values: Mapping[str, Any], order: list[str]) -> Any:
    """The value of the exit node that finished last (else the last finished node's value)."""
    finished = [n for n in order if n in values]
    exits = [n for n in finished if n in plan.exits]
    pick = exits or finished
    return values[pick[-1]] if pick else None


def max_steps(plan: ExecutablePlan, default: int = 25) -> int:
    budgets = plan.metadata.get("budgets") or {}
    return int(budgets.get("max_steps") or default)
