"""``compile_spec(spec) -> ExecutablePlan``: the bridge from a declarative spec to a runnable graph.

Steps:
1. Pick the PatternBuilder registered for ``spec.pattern`` and build the top-level fragment.
2. Expand every AGENT node with the PatternBuilder of its inner pattern (namespaced ``<node>.``).
3. Insert an APPROVAL node in front of every tool node whose tool requires approval.
4. Validate the plan (unique ids, edges, entry) and stamp metadata and a content hash.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from agent_contracts.interfaces import PatternBuilder, RuntimeAdapter
from agent_contracts.models import ExecutablePlan, NodeKind, PlanEdge, PlanFragment, PlanNode
from agent_contracts.registry import Registry
from agent_contracts.registry import registry as default_registry
from agent_spec.model import AgentSpec, NodeType
from agent_spec.platform import PlatformConfig

PLANNED_IN = {
    "prompt_chain": 15,
    "router": 15,
    "react": 13,
    "plan_execute": 14,
    "rewoo": 14,
    "reflection": 14,
    "evaluator_optimiser": 14,
    "agentic_rag": 16,
    "supervisor": 22,
    "hierarchical": 22,
    "handoff": 23,
    "group_chat": 23,
    "map_reduce": 23,
    "debate": 23,
}


class CompileError(Exception):
    pass


def _builder(reg: Registry, pattern: str) -> PatternBuilder:
    try:
        builder = reg.create("pattern_builder", pattern)
    except KeyError:
        step = PLANNED_IN.get(pattern)
        hint = f" (it arrives in Step {step})" if step else ""
        raise CompileError(f"no pattern builder installed for '{pattern}'{hint}") from None
    assert isinstance(builder, PatternBuilder)
    return builder


def _section(spec: AgentSpec) -> dict[str, Any]:
    if spec.graph is not None:
        return spec.graph.model_dump(mode="json", by_alias=True)
    return {"spec": spec.model_dump(mode="json", by_alias=True)}


def _expand_agents(reg: Registry, frag: PlanFragment, spec: AgentSpec) -> PlanFragment:
    nodes: list[PlanNode] = []
    edges = list(frag.edges)
    entry, exits = frag.entry, list(frag.exits)
    for node in frag.nodes:
        if node.kind is not NodeKind.AGENT or "loop" in node.config:  # loops are already built
            nodes.append(node)
            continue
        inner = _builder(reg, str(node.config["pattern"]))
        section = {
            **node.config,
            "budgets": spec.budgets.model_dump(),
            "models": spec.models,
            "approval_tools": [
                t.name for t in spec.tools if t.approval is not None and t.approval.required
            ],
        }
        sub = inner.build(section, f"{node.id}.")
        nodes.extend(sub.nodes)
        edges.extend(sub.edges)
        rewired: list[PlanEdge] = []
        for e in edges:
            if e.target == node.id:
                rewired.append(e.model_copy(update={"target": sub.entry}))
            elif e.source == node.id:
                rewired += [e.model_copy(update={"source": x}) for x in sub.exits]
            else:
                rewired.append(e)
        edges = rewired
        if entry == node.id:
            entry = sub.entry
        if node.id in exits:
            exits = [x for x in exits if x != node.id] + list(sub.exits)
    return PlanFragment(nodes=tuple(nodes), edges=tuple(edges), entry=entry, exits=tuple(exits))


def _insert_approvals(frag: PlanFragment, spec: AgentSpec) -> PlanFragment:
    nodes: list[PlanNode] = []
    edges = list(frag.edges)
    entry = frag.entry
    for node in frag.nodes:
        tool = spec.tool(str(node.config.get("tool"))) if node.kind is NodeKind.TOOL else None
        if tool is None or tool.approval is None or not tool.approval.required:
            nodes.append(node)
            continue
        gate = PlanNode(
            id=f"{node.id}.approval",
            kind=NodeKind.APPROVAL,
            config={"tool": tool.name, "ref": tool.ref, **tool.approval.model_dump()},
        )
        nodes += [gate, node]
        edges = [
            e.model_copy(update={"target": gate.id}) if e.target == node.id else e for e in edges
        ]
        edges.append(PlanEdge(source=gate.id, target=node.id, condition="on_ok"))
        if entry == node.id:
            entry = gate.id
    return PlanFragment(nodes=tuple(nodes), edges=tuple(edges), entry=entry, exits=frag.exits)


def _validate(frag: PlanFragment) -> None:
    ids = [n.id for n in frag.nodes]
    if len(ids) != len(set(ids)):
        raise CompileError("duplicate node ids after expansion")
    known = set(ids)
    for e in frag.edges:
        if e.source not in known or e.target not in known:
            raise CompileError(f"edge {e.source} -> {e.target} references an unknown node")
    if frag.entry not in known or not set(frag.exits) <= known:
        raise CompileError("plan entry or exits reference unknown nodes")


def plan_hash(plan: ExecutablePlan) -> str:
    body = plan.model_dump(mode="json", exclude={"metadata"})
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def check_capabilities(spec: AgentSpec, platform: PlatformConfig) -> None:
    """Refuse a model that lacks tool calling for any node that uses tools (Step 5)."""
    problems = []
    for node in spec.graph.nodes if spec.graph else ():
        if node.type not in (NodeType.AGENT, NodeType.LLM) or not node.tools:
            continue
        role = node.model or "default"
        alias = platform.alias_for(role, spec.models)
        if not platform.capabilities(alias).tool_calling:
            problems.append(
                f"node '{node.id}' uses tools but model '{alias}' (role '{role}') has no tool calling"
            )
    if not spec.graph and spec.tools:
        alias = platform.alias_for("default", spec.models)
        if not platform.capabilities(alias).tool_calling:
            problems.append(f"spec uses tools but default model '{alias}' has no tool calling")
    if problems:
        raise CompileError("; ".join(problems))


def check_tool_sources(spec: AgentSpec, platform: PlatformConfig) -> None:
    """Refuse ``mcp://<server>/...`` tools whose server is not on the platform allowlist (Step 7)."""
    unlisted = sorted(
        {
            t.ref.removeprefix("mcp://").split("/", 1)[0]
            for t in spec.tools
            if t.ref.startswith("mcp://")
        }
        - {s.name for s in platform.mcp.servers}
    )
    if unlisted:
        raise CompileError(
            f"MCP server(s) not on the allowlist (platform.yaml mcp.servers): {', '.join(unlisted)}"
        )


def compile_spec(
    spec: AgentSpec, reg: Registry | None = None, platform: PlatformConfig | None = None
) -> ExecutablePlan:
    reg = reg or default_registry
    if platform is not None:
        check_capabilities(spec, platform)
        check_tool_sources(spec, platform)
    frag = _builder(reg, spec.pattern.value).build(_section(spec), "")
    frag = _insert_approvals(_expand_agents(reg, frag, spec), spec)
    _validate(frag)
    plan = ExecutablePlan(
        name=spec.name,
        version=spec.version,
        nodes=frag.nodes,
        edges=frag.edges,
        entry=frag.entry,
        exits=frag.exits,
    )
    meta = {
        "spec_version": spec.spec_version,
        "owner": spec.owner,
        "risk_tier": spec.risk_tier.value,
        "autonomy": spec.autonomy.value,
        "pattern": spec.pattern.value,
        "runtime": spec.runtime.model_dump(mode="json"),
        "budgets": spec.budgets.model_dump(mode="json"),
        "models": dict(spec.models),
        "model_aliases": {
            r: platform.alias_for(r, spec.models) for r in {*spec.models, *platform.models.roles}
        }
        if platform is not None
        else {},
        "tools": [
            {
                "name": t.name,
                "ref": t.ref,
                "approval": t.approval is not None and t.approval.required,
                "approvers": t.approval.approvers if t.approval is not None else None,
                "rate_limit_per_minute": t.rate_limit_per_minute,
            }
            for t in spec.tools
        ],
        "guardrails": [g.name for g in spec.guardrails],
        "components": dict(spec.components),
        "plan_hash": plan_hash(plan),
    }
    return plan.model_copy(update={"metadata": meta})


def runtime_for(spec: AgentSpec, reg: Registry | None = None, **config: Any) -> RuntimeAdapter:
    reg = reg or default_registry
    name = spec.components.get("runtime_adapter", spec.runtime.adapter)
    try:
        adapter = reg.create("runtime_adapter", name, **config)
    except KeyError:
        installed = ", ".join(reg.names("runtime_adapter")) or "none"
        raise CompileError(
            f"runtime adapter '{name}' is not installed (installed: {installed})"
        ) from None
    assert isinstance(adapter, RuntimeAdapter)
    return adapter
