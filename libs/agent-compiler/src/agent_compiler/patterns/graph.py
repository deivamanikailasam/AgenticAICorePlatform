"""PatternBuilders for specs whose control flow is an explicit graph."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_contracts.interfaces import PatternBuilder
from agent_contracts.models import NodeKind, PlanEdge, PlanFragment, PlanNode

KIND = {
    "function": NodeKind.FUNCTION,
    "llm": NodeKind.LLM,
    "tool": NodeKind.TOOL,
    "agent": NodeKind.AGENT,
    "router": NodeKind.ROUTER,
    "approval": NodeKind.APPROVAL,
    "subgraph": NodeKind.SUBPLAN,
    "join": NodeKind.JOIN,
}
NODE_FIELDS = (
    "fn",
    "tool",
    "tools",
    "pattern",
    "model",
    "prompt",
    "routes",
    "spec",
    "approval",
    "max_iterations",
    "timeout_seconds",
    "compensation",
    "config",
)


class GraphPatternBuilder(PatternBuilder):
    """Translates ``graph: {entry, nodes, edges}`` one-to-one into plan nodes and edges.

    ``agent`` nodes are kept as single AGENT nodes here; the compiler expands each one with the
    PatternBuilder of its inner ``pattern``.
    """

    allow_agent_nodes = False

    def build(self, section: Mapping[str, Any], prefix: str) -> PlanFragment:
        nodes = []
        for raw in section["nodes"]:
            kind = KIND[raw["type"]]
            if kind is NodeKind.AGENT and not self.allow_agent_nodes:
                raise ValueError(f"node {raw['id']}: agent nodes need pattern 'hybrid'")
            config = {k: raw[k] for k in NODE_FIELDS if raw.get(k) not in (None, (), [], {})}
            nodes.append(PlanNode(id=f"{prefix}{raw['id']}", kind=kind, config=config))
        edges = tuple(
            PlanEdge(
                source=f"{prefix}{e['from']}", target=f"{prefix}{e['to']}", condition=e.get("when")
            )
            for e in section.get("edges", ())
        )
        ids = [n.id for n in nodes]
        has_incoming = {e.target for e in edges}
        entry = (
            f"{prefix}{section['entry']}"
            if section.get("entry")
            else next(i for i in ids if i not in has_incoming)
        )
        has_outgoing = {e.source for e in edges}
        exits = tuple(i for i in ids if i not in has_outgoing) or (ids[-1],)
        return PlanFragment(nodes=tuple(nodes), edges=edges, entry=entry, exits=exits)


class StateMachinePatternBuilder(GraphPatternBuilder):
    """Same translation; states are nodes and transitions are edges with outcomes."""


class HybridPatternBuilder(GraphPatternBuilder):
    """Workflow nodes and agent nodes mixed freely; agent nodes are expanded by the compiler."""

    allow_agent_nodes = True
