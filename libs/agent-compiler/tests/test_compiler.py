from pathlib import Path
from typing import Any, ClassVar

import pytest
from agent_compiler import CompileError, compile_spec, runtime_for, tracing
from agent_compiler.compiler import plan_hash
from agent_compiler.patterns.graph import GraphPatternBuilder, HybridPatternBuilder
from agent_compiler.run import resolve_function, run
from agent_contracts.interfaces import PatternBuilder
from agent_contracts.models import NodeKind, PlanFragment, PlanNode, RunContext, RunStatus
from agent_contracts.registry import Registry, registry
from agent_contracts.testing import PatternBuilderContract
from agent_spec import load_spec, load_spec_text
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

ROOT = Path(__file__).resolve().parents[3]
HELLO = ROOT / "agents/hello-workflow/agent.yaml"


class TestGraphPatternBuilderContract(PatternBuilderContract):
    section: ClassVar[dict[str, Any]] = {
        "nodes": [{"id": "a", "type": "function", "fn": "hello"}],
        "edges": [],
    }

    def make(self) -> PatternBuilder:
        return GraphPatternBuilder()


class TestHybridPatternBuilderContract(PatternBuilderContract):
    section: ClassVar[dict[str, Any]] = {
        "nodes": [
            {"id": "a", "type": "function", "fn": "hello"},
            {"id": "b", "type": "agent", "pattern": "react"},
        ],
        "edges": [{"from": "a", "to": "b", "when": None}],
    }

    def make(self) -> PatternBuilder:
        return HybridPatternBuilder()


def test_graph_builders_are_registered() -> None:
    assert {"workflow", "state_machine", "hybrid"} <= set(registry.names("pattern_builder"))


def test_hello_compiles_with_metadata_and_stable_hash() -> None:
    spec = load_spec(HELLO)
    plan = compile_spec(spec)
    assert [n.id for n in plan.nodes] == ["greet"]
    assert plan.entry == "greet"
    assert plan.metadata["risk_tier"] == "low"
    assert plan.metadata["plan_hash"] == plan_hash(plan) == compile_spec(spec).metadata["plan_hash"]


def _invoice(pattern: str) -> Any:
    text = (ROOT / "agents/invoice-triage/agent.yaml").read_text()
    return load_spec_text(text.replace("pattern: react", f"pattern: {pattern}"))


def test_missing_inner_pattern_names_the_step() -> None:
    reg = Registry()
    reg.discover()
    del reg._classes[("pattern_builder", "debate")]  # as if that step were not built yet
    with pytest.raises(
        CompileError,
        match=r"no pattern builder installed for 'debate' \(it arrives in Step 23\)",
    ):
        compile_spec(_invoice("debate"), reg)


def test_a_team_pattern_inside_a_graph_node_is_refused() -> None:
    with pytest.raises(ValueError, match="describes a whole team"):
        compile_spec(_invoice("supervisor"))


def test_react_agent_node_becomes_a_loop_with_an_approval_gate() -> None:
    plan = compile_spec(load_spec(ROOT / "agents/invoice-triage/agent.yaml"))
    loop = plan.node("resolve_exceptions.loop")
    assert loop.kind is NodeKind.AGENT
    assert loop.config["tools"] == ["get_invoice", "search"]
    assert loop.config["approval_tools"] == ["post_payment"]
    assert loop.config["model"] == "planner"
    assert loop.config["max_iterations"] == 25
    edges = {(e.source, e.target, e.condition) for e in plan.edges}
    assert ("validate", "resolve_exceptions.loop", "on_error") in edges
    assert ("resolve_exceptions.loop", "resolve_exceptions.approval", "needs_approval") in edges
    assert ("resolve_exceptions.approval", "resolve_exceptions.loop", None) in edges
    assert ("resolve_exceptions.loop", "prepare_payment", None) in edges


class _TwoStepAgent(PatternBuilder):
    def build(self, section: Any, prefix: str) -> PlanFragment:
        a = PlanNode(id=f"{prefix}think", kind=NodeKind.LLM)
        b = PlanNode(id=f"{prefix}act", kind=NodeKind.TOOL, config={"tool": "get_invoice"})
        from agent_contracts.models import PlanEdge

        return PlanFragment(
            nodes=(a, b), edges=(PlanEdge(source=a.id, target=b.id),), entry=a.id, exits=(b.id,)
        )


def test_agent_nodes_expand_and_irreversible_tools_get_approval() -> None:
    reg = Registry()
    reg.discover()
    del reg._classes[("pattern_builder", "debate")]  # stand in a two-step inner pattern
    reg.add("pattern_builder", "debate", _TwoStepAgent)
    plan = compile_spec(_invoice("debate"), reg)
    ids = [n.id for n in plan.nodes]
    assert "resolve_exceptions.think" in ids
    assert "resolve_exceptions" not in ids
    assert "pay.approval" in ids
    edges = {(e.source, e.target, e.condition) for e in plan.edges}
    assert ("validate", "resolve_exceptions.think", "on_error") in edges
    assert ("resolve_exceptions.act", "prepare_payment", None) in edges
    assert ("validate", "prepare_payment", "on_ok") in edges
    assert ("prepare_payment", "pay.approval", "on_ok") in edges
    assert ("pay.approval", "pay", "on_ok") in edges
    gate = plan.node("pay.approval")
    assert gate.kind is NodeKind.APPROVAL
    assert gate.config["approvers"] == "group:finance-approvers"
    assert plan.metadata["tools"][1] == {
        "name": "post_payment",
        "ref": "mcp://erp/post_payment",
        "approval": True,
        "approvers": "group:finance-approvers",
        "rate_limit_per_minute": None,
        "credentials": None,
    }


def test_unknown_runtime_adapter() -> None:
    spec = load_spec_text(HELLO.read_text().replace("adapter: langgraph", "adapter: warp-drive"))
    with pytest.raises(CompileError, match="runtime adapter 'warp-drive' is not installed"):
        runtime_for(spec)


def test_resolve_function() -> None:
    assert resolve_function("hello")({"input": {"name": "x"}}) == {"greeting": "hello, x"}
    assert resolve_function("json.dumps")([1]) == "[1]"
    with pytest.raises(LookupError):
        resolve_function("nodots")


async def test_run_emits_genai_spans() -> None:
    exporter = InMemorySpanExporter()
    provider = tracing.configure(exporter=exporter)
    ctx = RunContext(tenant_id="t1", user_id="u1", agent_id="hello-workflow")
    result, trace_id = await run(load_spec(HELLO), {"name": "Ada"}, ctx, provider)
    assert result.status is RunStatus.SUCCEEDED
    assert result.output == {"greeting": "hello, Ada"}
    spans = {s.name: s for s in exporter.get_finished_spans()}
    root = spans["invoke_agent hello-workflow"]
    assert root.attributes is not None
    assert root.attributes["gen_ai.operation.name"] == "invoke_agent"
    assert root.attributes["agent_platform.tenant.id"] == "t1"
    node = spans["execute_node greet"]
    assert node.parent is not None
    assert node.parent.span_id == root.context.span_id
    assert format(root.context.trace_id, "032x") == trace_id


def test_capability_check_refuses_model_without_tool_calling() -> None:
    from datetime import date

    from agent_compiler.compiler import check_capabilities
    from agent_contracts.models import ModelCapabilities
    from agent_spec.platform import Currency, Models, PlatformConfig

    roles = {
        r: "small" for r in ("default", "planner", "executor", "router", "judge", "summariser")
    }
    weak = PlatformConfig(
        models=Models(roles=roles, capabilities={"small": ModelCapabilities(tool_calling=False)}),
        currency=Currency(usd_to_aud=1.5, source="t", as_of=date(2026, 10, 1)),
    )
    spec = load_spec(ROOT / "agents/invoice-triage/agent.yaml")
    with pytest.raises(
        CompileError, match="node 'resolve_exceptions' uses tools but model 'local-qwen-quality'"
    ):
        check_capabilities(spec, weak)
    strong = weak.model_copy(
        update={
            "models": Models(
                roles=roles,
                capabilities={"local-qwen-quality": ModelCapabilities(tool_calling=True)},
            )
        }
    )
    check_capabilities(spec, strong)


def test_compile_with_platform_records_alias_per_role() -> None:
    from agent_spec.platform import load_platform

    plan = compile_spec(load_spec(HELLO), platform=load_platform())
    assert plan.metadata["model_aliases"]["planner"] == "local-qwen-quality"


def test_unlisted_mcp_servers_are_refused_at_compile_time() -> None:
    from agent_compiler.compiler import check_tool_sources
    from agent_spec.platform import Mcp, McpServer, load_platform

    platform = load_platform()
    spec = load_spec(ROOT / "agents/invoice-triage/agent.yaml")
    with pytest.raises(CompileError, match=r"not on the allowlist .*: erp, rag-core"):
        check_tool_sources(spec, platform)
    listed = platform.model_copy(
        update={
            "mcp": Mcp(
                servers=[
                    McpServer(name=n, transport="http", url="http://x") for n in ("erp", "rag-core")
                ]
            )
        }
    )
    check_tool_sources(spec, listed)


def test_durability_temporal_selects_the_temporal_runtime() -> None:
    from agent_runtime.temporal.runtime import TemporalRuntime

    text = HELLO.read_text().replace("durability: memory", "durability: temporal")
    assert isinstance(runtime_for(load_spec_text(text)), TemporalRuntime)
    pinned = text.replace("runtime: {", "components: { runtime_adapter: langgraph }\nruntime: {")
    assert not isinstance(runtime_for(load_spec_text(pinned)), TemporalRuntime)


CUSTOM = """
spec_version: 1
name: team-pattern
version: 0.1.0
owner: team-a
risk_tier: medium
autonomy: agent
pattern: custom
components: { pattern_builder: debate }
budgets: { max_steps: 5, max_wall_seconds: 30 }
tools:
  - ref: mcp://erp/get_invoice
  - ref: mcp://erp/post_payment
    approval: { required: true, approvers: "group:finance-approvers" }
"""


class _PaysDirectly(PatternBuilder):
    """A team's builder that tries to call an irreversible tool with no approval in its plan."""

    def build(self, section: Any, prefix: str) -> PlanFragment:
        assert section["spec"]["name"] == "team-pattern"
        pay = PlanNode(id=f"{prefix}pay", kind=NodeKind.TOOL, config={"tool": "post_payment"})
        return PlanFragment(nodes=(pay,), edges=(), entry=pay.id, exits=(pay.id,))


def test_custom_pattern_uses_the_named_builder_and_keeps_the_approval_gate() -> None:
    reg = Registry()
    reg.discover()
    del reg._classes[("pattern_builder", "debate")]
    reg.add("pattern_builder", "debate", _PaysDirectly)
    plan = compile_spec(load_spec_text(CUSTOM), reg)
    assert plan.entry == "pay.approval"
    assert plan.node("pay.approval").kind is NodeKind.APPROVAL
    assert plan.metadata["pattern"] == "custom"


def test_custom_pattern_without_a_builder_is_refused() -> None:
    from agent_spec import SpecError

    with pytest.raises(SpecError, match=r"components\.pattern_builder"):
        load_spec_text(CUSTOM.replace("components: { pattern_builder: debate }\n", ""))
    with pytest.raises(SpecError, match="no pattern_builder named 'nope'"):
        load_spec_text(CUSTOM.replace("pattern_builder: debate", "pattern_builder: nope"))
