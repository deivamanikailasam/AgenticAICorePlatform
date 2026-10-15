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
            if "compensation" in config:
                config["compensation"] = f"{prefix}{config['compensation']}"
            nodes.append(PlanNode(id=f"{prefix}{raw['id']}", kind=kind, config=config))
        edges = tuple(
            PlanEdge(
                source=f"{prefix}{e['from']}", target=f"{prefix}{e['to']}", condition=e.get("when")
            )
            for e in section.get("edges", ())
        )
        compensators = {
            f"{prefix}{raw['compensation']}" for raw in section["nodes"] if raw.get("compensation")
        }
        ids = [n.id for n in nodes if n.id not in compensators]
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


CHAIN_KINDS = {"llm", "function", "tool", "approval"}
NEXT = (None, "on_ok")


class PromptChainPatternBuilder(GraphPatternBuilder):
    """``pattern: prompt_chain`` (Step 15): a straight line of steps (LLM, function, tool), each
    LLM step that feeds another step guarded by a validation gate (``config.gate``). ``on_error``
    edges may leave the line (a gate that keeps failing ends there)."""

    def build(self, section: Mapping[str, Any], prefix: str) -> PlanFragment:
        nodes = {raw["id"]: raw for raw in section["nodes"]}
        for raw in section["nodes"]:
            if raw["type"] not in CHAIN_KINDS:
                raise ValueError(
                    f"node {raw['id']}: a prompt chain has llm, function, tool and approval "
                    f"steps, not {raw['type']} (use pattern 'workflow', 'router' or 'hybrid')"
                )
        nexts: dict[str, list[str]] = {}
        prevs: dict[str, list[str]] = {}
        for e in section.get("edges", ()):
            if e.get("when") in NEXT:
                nexts.setdefault(e["from"], []).append(e["to"])
                prevs.setdefault(e["to"], []).append(e["from"])
            elif e.get("when") != "on_error":
                raise ValueError(
                    f"edge {e['from']} -> {e['to']}: a prompt chain only branches on on_error"
                )
        for name, targets in [*nexts.items(), *prevs.items()]:
            if len(targets) > 1:
                raise ValueError(f"node {name}: a prompt chain is a straight line, not a graph")
        on_error = {e["from"] for e in section.get("edges", ()) if e.get("when") == "on_error"}
        for e in section.get("edges", ()):
            if e["from"] in on_error and e.get("when") is None:
                raise ValueError(
                    f"edge {e['from']} -> {e['to']}: {e['from']} has an on_error exit, so its "
                    "next step must be 'on_ok' (an unconditional edge would also run on errors)"
                )
        for name in nexts:
            cfg = nodes[name].get("config") or {}
            if nodes[name]["type"] == "llm" and not cfg.get("gate"):
                raise ValueError(
                    f"node {name}: an LLM step that feeds the next step needs a validation gate "
                    "(config.gate)"
                )
        return super().build(section, prefix)


class RouterPatternBuilder(GraphPatternBuilder):
    """``pattern: router`` (Step 15): the entry is a router node and every route it may choose
    leads somewhere; handlers can be any node, including agents."""

    allow_agent_nodes = True

    def build(self, section: Mapping[str, Any], prefix: str) -> PlanFragment:
        frag = super().build(section, prefix)
        entry = next(n for n in frag.nodes if n.id == frag.entry)
        if entry.kind is not NodeKind.ROUTER:
            raise ValueError(f"pattern 'router' needs a router node as the entry, not {entry.id}")
        handled = {e.condition for e in frag.edges if e.source == entry.id}
        missing = [r for r in entry.config.get("routes", ()) if r not in handled]
        if missing:
            raise ValueError(f"router {entry.id}: no edge for routes {missing}")
        return frag
