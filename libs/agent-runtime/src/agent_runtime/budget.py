"""Budgets and loop control (Step 26).

``PlatformBudgetController`` enforces, per run, steps, tokens, AUD cost, wall-clock time, tool
calls (in total and per tool) and delegation depth. Budgets form a tree: a child run (a team
worker, a sub-plan) opens its budget under its parent, its spending counts against every
ancestor, and it stops as soon as any ancestor trips, so no run can outspend its parent.
Wall-clock time is measured from ``open`` (monotonic), so a run that stops reporting still
times out at its next charge.

``PlatformLoopDetector`` flags the three runaway shapes:

* the same call (tool and canonical arguments) repeated ``repeat`` times in the last
  ``window`` steps;
* ``no_progress`` consecutive steps that changed nothing;
* oscillation: the last steps alternate between two signatures (A B A B ...), ``oscillation``
  times.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from agent_contracts.interfaces import BudgetController, LoopDetector
from agent_contracts.models import (
    BudgetCharge,
    BudgetLimits,
    BudgetStatus,
    LoopVerdict,
    StepObservation,
)


@dataclass
class _Budget:
    limits: BudgetLimits
    parent: str | None
    depth: int
    started: float
    steps: int = 0
    tokens: int = 0
    cost_aud: float = 0.0
    tool_calls: int = 0
    per_tool: dict[str, int] = field(default_factory=dict)
    charged_seconds: float = 0.0
    tripped: str | None = None
    detail: str | None = None


class PlatformBudgetController(BudgetController):
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._b: dict[str, _Budget] = {}

    def _chain(self, run_id: str) -> list[tuple[str, _Budget]]:
        chain: list[tuple[str, _Budget]] = []
        rid: str | None = run_id
        while rid is not None and rid in self._b:
            chain.append((rid, self._b[rid]))
            rid = self._b[rid].parent
        return chain

    def _elapsed(self, b: _Budget) -> float:
        return max(b.charged_seconds, self.clock() - b.started)

    def _status(self, run_id: str) -> BudgetStatus:
        b = self._b[run_id]
        tripped = next((x.tripped for _, x in self._chain(run_id) if x.tripped), None)
        detail = next((x.detail for _, x in self._chain(run_id) if x.tripped), None)
        return BudgetStatus(
            run_id=run_id,
            ok=tripped is None,
            tripped=tripped,
            detail=detail,
            steps=b.steps,
            tokens=b.tokens,
            cost_aud=round(b.cost_aud, 8),
            tool_calls=b.tool_calls,
            elapsed_seconds=round(self._elapsed(b), 3),
            depth=b.depth,
        )

    async def open(
        self, run_id: str, limits: BudgetLimits, parent_run_id: str | None = None
    ) -> BudgetStatus:
        if run_id in self._b:
            return self._status(run_id)
        parent = self._b.get(parent_run_id) if parent_run_id else None
        depth = parent.depth + 1 if parent else 0
        b = _Budget(limits, parent_run_id if parent else None, depth, self.clock())
        self._b[run_id] = b
        for rid, ancestor in self._chain(run_id)[1:]:
            cap = ancestor.limits.max_delegation_depth
            if cap is not None and depth - ancestor.depth > cap:
                b.tripped = "max_delegation_depth"
                b.detail = f"max_delegation_depth: {depth - ancestor.depth} levels below {rid} (limit {cap})"
                break
        return self._status(run_id)

    async def charge(self, run_id: str, charge: BudgetCharge) -> BudgetStatus:
        if run_id not in self._b:
            raise KeyError(f"no budget open for run {run_id}")
        for _, b in self._chain(run_id):
            b.steps += charge.steps
            b.tokens += charge.usage.input_tokens + charge.usage.output_tokens
            b.cost_aud += charge.usage.cost_aud
            b.tool_calls += charge.tool_calls
            b.charged_seconds += charge.elapsed_seconds
            if charge.tool and charge.tool_calls:
                b.per_tool[charge.tool] = b.per_tool.get(charge.tool, 0) + charge.tool_calls
            if b.tripped is None:
                b.tripped, b.detail = self._check(b, charge.tool)
        return self._status(run_id)

    def _check(self, b: _Budget, tool: str | None) -> tuple[str | None, str | None]:
        lim = b.limits
        checks: list[tuple[str, float | int | None, float | int, str]] = [
            ("max_steps", lim.max_steps, b.steps, "{v} steps"),
            ("max_tokens", lim.max_tokens, b.tokens, "{v} tokens"),
            ("max_cost_aud", lim.max_cost_aud, b.cost_aud, "AUD {v:.4f}"),
            ("max_tool_calls", lim.max_tool_calls, b.tool_calls, "{v} tool calls"),
            ("max_wall_seconds", lim.max_wall_seconds, self._elapsed(b), "{v:.1f} s"),
        ]
        for name, cap, value, fmt in checks:
            if cap is not None and value > cap:
                return name, f"{name}: {fmt.format(v=value)} over the limit of {cap}"
        if tool and tool in lim.max_calls_per_tool:
            cap_t = lim.max_calls_per_tool[tool]
            if b.per_tool.get(tool, 0) > cap_t:
                return (
                    "max_calls_per_tool",
                    f"max_calls_per_tool: {b.per_tool[tool]} calls to {tool} over the limit of {cap_t}",
                )
        return None, None

    async def status(self, run_id: str) -> BudgetStatus:
        return self._status(run_id)

    def close(self, run_id: str) -> None:
        """Forget a finished run (and nothing else: children may still report to it)."""
        self._b.pop(run_id, None)


class PlatformLoopDetector(LoopDetector):
    def __init__(
        self, repeat: int = 3, window: int = 8, no_progress: int = 6, oscillation: int = 3
    ) -> None:
        self.repeat, self.window = repeat, window
        self.no_progress, self.oscillation = no_progress, oscillation
        self._seen: dict[str, deque[str]] = {}
        self._stalled: dict[str, int] = {}

    def observe(self, run_id: str, step: StepObservation) -> LoopVerdict:
        seen = self._seen.setdefault(run_id, deque(maxlen=max(self.window, 2 * self.oscillation)))
        seen.append(step.signature)
        self._stalled[run_id] = 0 if step.progress else self._stalled.get(run_id, 0) + 1
        recent = list(seen)[-self.window :]
        if recent.count(step.signature) >= self.repeat and not step.progress:
            return LoopVerdict(
                loop=True,
                reason=f"repeated call: {step.signature[:80]} {recent.count(step.signature)} times "
                f"in the last {len(recent)} steps",
            )
        if self._stalled[run_id] >= self.no_progress:
            return LoopVerdict(loop=True, reason=f"no progress for {self._stalled[run_id]} steps")
        tail = list(seen)[-2 * self.oscillation :]
        if (
            len(tail) == 2 * self.oscillation
            and tail[0] != tail[1]
            and all(tail[i] == tail[i % 2] for i in range(len(tail)))
        ):
            return LoopVerdict(
                loop=True,
                reason=f"oscillating between two steps ({tail[0][:40]} / {tail[1][:40]}) "
                f"{self.oscillation} times",
            )
        return LoopVerdict(loop=False)

    def reset(self, run_id: str) -> None:
        self._seen.pop(run_id, None)
        self._stalled.pop(run_id, None)
