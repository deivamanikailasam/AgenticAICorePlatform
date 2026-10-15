"""Rules a schema cannot express: references between parts of the spec, graph shape, safety."""

from __future__ import annotations

from collections import defaultdict

from agent_contracts.interfaces import INTERFACES
from agent_contracts.models import RiskTier
from agent_contracts.registry import registry

from agent_spec.errors import SpecIssue
from agent_spec.model import AgentSpec, Autonomy, Graph, NodeType, Pattern

AGENTIC_PATTERNS = {
    Pattern.REACT,
    Pattern.PLAN_EXECUTE,
    Pattern.REWOO,
    Pattern.REFLECTION,
    Pattern.EVALUATOR_OPTIMISER,
    Pattern.AGENTIC_RAG,
    Pattern.SUPERVISOR,
    Pattern.HIERARCHICAL,
    Pattern.HANDOFF,
    Pattern.GROUP_CHAT,
    Pattern.DEBATE,
}
MULTI_AGENT_PATTERNS = {
    Pattern.SUPERVISOR,
    Pattern.HIERARCHICAL,
    Pattern.HANDOFF,
    Pattern.GROUP_CHAT,
    Pattern.DEBATE,
}
GRAPH_PATTERNS = {Pattern.WORKFLOW, Pattern.STATE_MACHINE, Pattern.HYBRID}
# The platform's guardrail vocabulary (Step 27 implements each); registered guardrails also count.
KNOWN_GUARDRAILS = {
    "prompt_injection",
    "pii_redaction",
    "tool_args_schema",
    "output_schema",
    "secret_leak",
    "topic_filter",
    "safety_filter",
}


def _node_issues(spec: AgentSpec, graph: Graph) -> list[SpecIssue]:
    issues: list[SpecIssue] = []
    ids = [n.id for n in graph.nodes]
    nodes = {n.id: n for n in graph.nodes}
    for n in graph.nodes:
        where = f"graph.nodes[{ids.index(n.id)}]"
        if n.compensation and n.compensation not in nodes:
            issues.append(
                SpecIssue(f"{where}.compensation", f"node '{n.compensation}' does not exist")
            )
        if n.model and n.model not in spec.models:
            issues.append(
                SpecIssue(
                    f"{where}.model",
                    f"model role '{n.model}' is not defined in models ({', '.join(sorted(spec.models))})",
                )
            )
        if n.type is NodeType.AGENT and spec.autonomy is Autonomy.WORKFLOW:
            issues.append(
                SpecIssue(where, "agent nodes are not allowed when autonomy is 'workflow'")
            )
        for t in ((n.tool,) if n.tool else ()) + n.tools:
            if spec.tool(t) is None:
                known = ", ".join(x.name for x in spec.tools) or "none declared"
                issues.append(SpecIssue(where, f"tool '{t}' is not declared in tools ({known})"))
    return issues


def _graph_issues(spec: AgentSpec, graph: Graph) -> list[SpecIssue]:
    issues = _node_issues(spec, graph)
    ids = [n.id for n in graph.nodes]
    nodes = {n.id: n for n in graph.nodes}
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        issues.append(SpecIssue("graph.nodes", f"node id '{dup}' is used more than once"))

    out: dict[str, list[str]] = defaultdict(list)
    incoming: dict[str, int] = defaultdict(int)
    for i, e in enumerate(graph.edges):
        for end, label in ((e.source, "from"), (e.target, "to")):
            if end not in nodes:
                issues.append(
                    SpecIssue(f"graph.edges[{i}]", f"'{label}' node '{end}' does not exist")
                )
        if e.source in nodes and e.target in nodes:
            out[e.source].append(e.target)
            incoming[e.target] += 1
            src = nodes[e.source]
            if src.type is NodeType.ROUTER and e.when not in (*src.routes, "on_error", None):
                issues.append(
                    SpecIssue(
                        f"graph.edges[{i}].when",
                        f"router '{src.id}' has no route '{e.when}'; routes: {', '.join(src.routes)}",
                    )
                )

    if graph.entry is not None:
        entry = graph.entry
        if entry not in nodes:
            issues.append(SpecIssue("graph.entry", f"entry node '{entry}' does not exist"))
            return issues
    else:
        roots = [n for n in ids if incoming[n] == 0]
        if len(roots) != 1:
            issues.append(
                SpecIssue(
                    "graph.entry",
                    f"cannot tell where the graph starts ({len(roots)} nodes have no incoming edge: "
                    f"{', '.join(roots) or 'none'}); set graph.entry",
                )
            )
            return issues
        entry = roots[0]

    seen: set[str] = set()
    stack = [entry]
    while stack:
        n = stack.pop()
        if n not in seen:
            seen.add(n)
            stack.extend(out[n])
    for n in ids:
        if n not in seen:
            issues.append(
                SpecIssue("graph.nodes", f"node '{n}' can never run (not reachable from '{entry}')")
            )

    # every cycle must pass through a node with a loop bound
    colour: dict[str, int] = {}

    def visit(n: str, trail: list[str]) -> None:
        colour[n] = 1
        for m in out[n]:
            if colour.get(m) == 1:
                cycle = [*trail[trail.index(m) :], n] if m in trail else [m, n]
                if not any(nodes[c].max_iterations for c in cycle):
                    issues.append(
                        SpecIssue(
                            "graph.edges",
                            f"cycle {' -> '.join([*cycle, m])} has no loop bound; set max_iterations "
                            "on one of its nodes",
                        )
                    )
            elif m not in colour:
                visit(m, [*trail, n])
        colour[n] = 2

    visit(entry, [])

    return issues


def semantic_issues(spec: AgentSpec) -> list[SpecIssue]:
    issues: list[SpecIssue] = []

    if spec.pattern in GRAPH_PATTERNS and spec.graph is None:
        issues.append(SpecIssue("graph", f"pattern '{spec.pattern.value}' needs a graph"))
    if spec.autonomy is Autonomy.WORKFLOW and spec.pattern in AGENTIC_PATTERNS:
        issues.append(
            SpecIssue(
                "pattern",
                f"pattern '{spec.pattern.value}' is agentic; "
                "autonomy 'workflow' allows no agentic decisions",
            )
        )
    if spec.pattern in MULTI_AGENT_PATTERNS and spec.autonomy is not Autonomy.MULTI_AGENT:
        issues.append(
            SpecIssue("autonomy", f"pattern '{spec.pattern.value}' needs autonomy 'multi_agent'")
        )
    if (
        spec.autonomy is Autonomy.MULTI_AGENT
        and not spec.agents
        and not (spec.graph and any(n.type is NodeType.AGENT for n in spec.graph.nodes))
    ):
        issues.append(SpecIssue("agents", "autonomy 'multi_agent' needs agents or agent nodes"))

    if spec.graph is not None:
        issues += _graph_issues(spec, spec.graph)

    uses_models = spec.pattern in AGENTIC_PATTERNS or bool(
        spec.graph
        and any(n.type in (NodeType.AGENT, NodeType.LLM, NodeType.ROUTER) for n in spec.graph.nodes)
    )
    if uses_models:
        for limit in ("max_tokens", "max_cost_aud"):
            if getattr(spec.budgets, limit) is None:
                issues.append(SpecIssue(f"budgets.{limit}", "required when the spec calls models"))

    names = [t.name for t in spec.tools]
    for dup in sorted({n for n in names if names.count(n) > 1}):
        issues.append(SpecIssue("tools", f"two tools are both called '{dup}'; give one an alias"))
    if spec.graph is not None:
        used = {t for n in spec.graph.nodes for t in ((n.tool,) if n.tool else ()) + n.tools}
        for t in spec.tools:
            if t.name not in used and spec.pattern in GRAPH_PATTERNS:
                issues.append(
                    SpecIssue(
                        "tools",
                        f"tool '{t.name}' is declared but no node uses it",
                        severity="warning",
                    )
                )

    allowed_guardrails = KNOWN_GUARDRAILS | set(registry.names("guardrail"))
    for i, g in enumerate(spec.guardrails):
        if g.name not in allowed_guardrails:
            issues.append(
                SpecIssue(
                    f"guardrails[{i}]",
                    f"unknown guardrail '{g.name}'; known: {', '.join(sorted(allowed_guardrails))}",
                )
            )

    for slot, name in spec.components.items():
        if slot not in INTERFACES:
            issues.append(SpecIssue(f"components.{slot}", f"unknown slot '{slot}'"))
        elif name not in registry.names(slot):
            options = ", ".join(registry.names(slot)) or "none installed"
            issues.append(
                SpecIssue(f"components.{slot}", f"no {slot} named '{name}' (installed: {options})")
            )

    agent_names = [a.name for a in spec.agents]
    for dup in sorted({a for a in agent_names if agent_names.count(a) > 1}):
        issues.append(SpecIssue("agents", f"agent name '{dup}' is used more than once"))

    if spec.risk_tier is RiskTier.HIGH and spec.evals is None:
        issues.append(SpecIssue("evals", "high-risk specs must name an eval suite"))
    if spec.risk_tier is not RiskTier.LOW and spec.tools and not spec.guardrails:
        issues.append(
            SpecIssue(
                "guardrails",
                "medium/high-risk specs with tools should enable "
                "guardrails (at least prompt_injection)",
                severity="warning",
            )
        )
    return issues
