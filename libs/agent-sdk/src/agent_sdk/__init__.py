"""The platform's public SDK (Step 39).

What a team needs to extend the platform from its own package, all registered as entry points
(the template's ``pyproject.toml`` shows each):

* **Tools**: ``@tool(...)`` on an async function, group ``agent_platform.components``, name
  ``tool.<name>``. Granted by ``ref`` in ``agent.yaml``.
* **Functions** for ``function`` nodes and gates: ``(state: dict) -> value``, group
  ``agent_platform.functions``.
* **Patterns**: a ``PatternBuilder`` subclass, group ``agent_platform.components``, name
  ``pattern_builder.<name>``; used with ``pattern: custom`` and ``components.pattern_builder``.
* **Evaluators**: ``(config, task, observation) -> Score``, group ``agent_platform.evaluators``;
  named in a golden task's ``expected.custom``.

Names here are the stable surface: import from ``agent_sdk``, not from the platform's internal
packages, which may change.
"""

from agent_contracts.interfaces import PatternBuilder, Tool
from agent_contracts.models import (
    DataClass,
    NodeKind,
    PlanEdge,
    PlanFragment,
    PlanNode,
    RunContext,
    SideEffect,
    ToolSpec,
)
from agent_evals.checks import Evaluator, Observation, Score
from agent_evals.tasks import Task

from agent_sdk.tools import ToolFunction, tool

__all__ = [
    "DataClass",
    "Evaluator",
    "NodeKind",
    "Observation",
    "PatternBuilder",
    "PlanEdge",
    "PlanFragment",
    "PlanNode",
    "RunContext",
    "Score",
    "SideEffect",
    "Task",
    "Tool",
    "ToolFunction",
    "ToolSpec",
    "tool",
]
