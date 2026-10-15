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
  outcome with no matching edge fails the run;
* **saga** (Step 12): when a run fails, every finished node that names a ``compensation`` node
  has that node run, newest first, so completed writes are undone;
* **journal and replay** (Step 12): every node result is recorded per run, in order of
  occurrence. A runner built with ``replay=journal`` returns the recorded results instead of
  executing anything, so a replay takes exactly the same path with no model or tool calls.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from agent_contracts.models import ExecutablePlan, NodeKind, PlanNode, RunContext

NodeFn = Callable[[dict[str, Any]], Any | Awaitable[Any]]
#: Runs a non-function node: (node, values, ctx) -> value; raise to fail; return
#: {"_outcome": ...} to choose a branch.
NodeExecutor = Callable[[PlanNode, dict[str, Any], RunContext], Awaitable[Any]]


class StopRun(Exception):  # noqa: N818 - a control signal, not an error
    """Raised by a node that hit a limit (``max_steps`` in an agent loop, a budget): the run ends
    with status ``stopped`` and keeps its partial state, unless a ``stopped`` edge handles it."""

    def __init__(self, reason: str, partial: Any = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.partial = partial


@dataclass(frozen=True)
class NodeOutcome:
    outcome: str
    value: Any
    error: str | None = None


@dataclass
class RunJournal:
    """The results of one run's nodes, in order of occurrence per node (loops repeat a node)."""

    run_id: str
    entries: dict[str, list[NodeOutcome]] = field(default_factory=dict)
    _cursor: dict[str, int] = field(default_factory=dict, repr=False)

    def record(self, node_id: str, outcome: NodeOutcome) -> None:
        self.entries.setdefault(node_id, []).append(outcome)

    def next(self, node_id: str) -> NodeOutcome:
        i = self._cursor.get(node_id, 0)
        recorded = self.entries.get(node_id, [])
        if i >= len(recorded):
            raise LookupError(f"replay diverged: node '{node_id}' ran more often than recorded")
        self._cursor[node_id] = i + 1
        return recorded[i]

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "entries": {
                k: [{"outcome": o.outcome, "value": o.value, "error": o.error} for o in v]
                for k, v in self.entries.items()
            },
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> RunJournal:
        return cls(
            run_id=str(data["run_id"]),
            entries={
                k: [NodeOutcome(e["outcome"], e["value"], e.get("error")) for e in v]
                for k, v in data["entries"].items()
            },
        )


def approval_outcome(payload: Mapping[str, Any]) -> NodeOutcome:
    return NodeOutcome("ok" if payload.get("approved") else "rejected", dict(payload))


class NodeRunner:
    def __init__(
        self,
        functions: Mapping[str, NodeFn] | None = None,
        executors: Mapping[NodeKind, NodeExecutor] | None = None,
        replay: RunJournal | None = None,
    ) -> None:
        self.functions = dict(functions or {})
        self.executors = dict(executors or {})
        self.replay = replay
        self.journals: dict[str, RunJournal] = {}

    def journal(self, run_id: str) -> RunJournal:
        return self.journals.setdefault(run_id, RunJournal(run_id))

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
        if self.replay is not None:
            try:
                result = self.replay.next(node.id)
            except LookupError as exc:
                result = NodeOutcome("error", {"error": str(exc)}, str(exc))
        else:
            result = await self._run(node, values, ctx)
        self.journal(ctx.run_id).record(node.id, result)
        return result

    def record(self, ctx: RunContext, node_id: str, result: NodeOutcome) -> NodeOutcome:
        """For results adapters produce themselves (approvals), so replays see them too."""
        self.journal(ctx.run_id).record(node_id, result)
        return result

    async def _run(self, node: PlanNode, values: dict[str, Any], ctx: RunContext) -> NodeOutcome:
        timeout = node.config.get("timeout_seconds")
        try:
            call = self._call(node, values, ctx)
            result = await (asyncio.wait_for(call, float(timeout)) if timeout else call)
        except TimeoutError:
            return NodeOutcome("error", {"error": f"timed out after {timeout}s"}, "timeout")
        except StopRun as stop:
            return NodeOutcome(
                "stopped", {"stopped": stop.reason, "partial": stop.partial}, stop.reason
            )
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


async def compensate(
    plan: ExecutablePlan,
    runner: NodeRunner,
    values: dict[str, Any],
    order: list[str],
    outcomes: Mapping[str, str],
    ctx: RunContext,
) -> list[tuple[str, NodeOutcome]]:
    """Run the compensation of every finished node that has one, newest first (saga).

    A step that itself failed has nothing to undo. Compensations see the run's values plus
    ``compensating`` (the id of the step being undone); their results are stored under their
    own node ids.
    """
    done: list[tuple[str, NodeOutcome]] = []
    for node_id in reversed(order):
        target = plan.node(node_id).config.get("compensation")
        if not target or outcomes.get(node_id) == "error":
            continue
        comp = plan.node(str(target))
        result = await runner.run(comp, {**values, "compensating": node_id}, ctx)
        values[comp.id] = result.value
        done.append((comp.id, result))
    return done
