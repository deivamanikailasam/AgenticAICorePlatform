"""Agentic patterns built as one agent node: ``react`` (Step 13) and the planning and reflection
patterns ``plan_execute``, ``rewoo``, ``reflection``, ``evaluator_optimiser`` (Step 14).

Builds two nodes: ``<prefix>loop`` (an AGENT node run by the agent-loop executor) and
``<prefix>approval``, which the loop routes to when a tool that needs approval is called; the
approval always returns to the loop, which then executes or drops the pending calls.

Used for a whole spec (``pattern: react`` without a graph: every spec tool, the ``default``
model) or for an ``agent`` node inside a hybrid graph (its ``tools``, ``model``, ``config``,
``max_iterations``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_contracts.interfaces import PatternBuilder
from agent_contracts.models import NodeKind, PlanEdge, PlanFragment, PlanNode

#: strategy settings lifted from an agent node's ``config`` onto the plan node
STRATEGY_SETTINGS = ("max_rounds", "max_replans", "executor_model")


class ReactPatternBuilder(PatternBuilder):
    loop = "react"

    def build(self, section: Mapping[str, Any], prefix: str) -> PlanFragment:
        if "spec" in section:  # the whole spec is one agent
            spec = section["spec"]
            tools = [
                t.get("alias")
                or t["ref"].rstrip("/").rsplit("/", 1)[-1].replace("-", "_").replace(".", "_")
                for t in spec["tools"]
            ]  # the same names ToolRef.name gives
            approval_tools = [
                tools[i]
                for i, t in enumerate(spec["tools"])
                if (t.get("approval") or {}).get("required")
            ]
            model, config, limit = "default", {}, spec["budgets"]["max_steps"]
        else:
            tools = list(section.get("tools", ()))
            approval_tools = list(section.get("approval_tools", ()))
            model = section.get("model") or "default"
            config = dict(section.get("config") or {})
            limit = section.get("max_iterations") or section["budgets"]["max_steps"]
        loop_id, gate_id = f"{prefix}loop", f"{prefix}approval"
        loop = PlanNode(
            id=loop_id,
            kind=NodeKind.AGENT,
            config={
                **{k: config[k] for k in STRATEGY_SETTINGS if k in config},
                "loop": self.loop,
                "tools": tools,
                "approval_tools": approval_tools,
                "model": model,
                "config": config,
                "max_iterations": int(limit),
                "approval_node": gate_id,
            },
        )
        gate = PlanNode(id=gate_id, kind=NodeKind.APPROVAL, config={"agent": loop_id})
        return PlanFragment(
            nodes=(loop, gate),
            edges=(
                PlanEdge(source=loop_id, target=gate_id, condition="needs_approval"),
                PlanEdge(source=gate_id, target=loop_id),
            ),
            entry=loop_id,
            exits=(loop_id,),
        )


class PlanExecutePatternBuilder(ReactPatternBuilder):
    """Plan, carry out each step with a small ReAct run, re-plan on failure."""

    loop = "plan_execute"


class RewooPatternBuilder(ReactPatternBuilder):
    """Plan once with tool arguments, run the tools without re-prompting, solve once."""

    loop = "rewoo"


class ReflectionPatternBuilder(ReactPatternBuilder):
    """Draft, self-critique, revise; bounded rounds."""

    loop = "reflection"


class EvaluatorOptimiserPatternBuilder(ReactPatternBuilder):
    """Optimiser drafts, separate evaluator scores, keep the best; bounded rounds."""

    loop = "evaluator_optimiser"
