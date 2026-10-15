"""Multi-agent teams in one process (Step 22): ``supervisor`` and ``hierarchical``.

Both build one SUPERVISOR node that delegates to the spec's ``agents``. Each worker is another
agent.yaml, run as a child run with its own tools, grants, model roles and budget; a worker can
itself be a supervisor, which is what makes a hierarchy (nesting is bounded by ``max_depth``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_contracts.interfaces import PatternBuilder
from agent_contracts.models import NodeKind, PlanFragment, PlanNode


class SupervisorPatternBuilder(PatternBuilder):
    def build(self, section: Mapping[str, Any], prefix: str) -> PlanFragment:
        if "spec" not in section:
            raise ValueError(
                "pattern 'supervisor' describes a whole team: use it as the spec's pattern with "
                "agents:, and nest a team elsewhere as a subplan node"
            )
        spec = section["spec"]
        settings = dict(spec.get("supervisor") or {})
        node = PlanNode(
            id=f"{prefix}supervisor",
            kind=NodeKind.SUPERVISOR,
            config={
                **settings,
                "workers": [
                    {k: a.get(k) for k in ("name", "spec", "role", "reads", "writes")}
                    for a in spec["agents"]
                ],
                "max_cost_aud": spec["budgets"].get("max_cost_aud"),
                "team": spec["name"],
            },
        )
        return PlanFragment(nodes=(node,), edges=(), entry=node.id, exits=(node.id,))


class HierarchicalPatternBuilder(SupervisorPatternBuilder):
    """Same node: workers that are supervisors themselves form the hierarchy."""
