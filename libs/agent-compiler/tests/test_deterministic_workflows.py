"""Step 12: deterministic workflows from agent.yaml, on both runtime adapters.

Done when: branches run in parallel; a failed step triggers its compensation; replay produces
the same path. Also: LLM, router and tool nodes, sub-workflows and node timeouts.
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from agent_compiler.run import run
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.interfaces import ModelClient
from agent_contracts.models import (
    ChatRequest,
    ChatResponse,
    Message,
    ModelCapabilities,
    Role,
    RunStatus,
)
from agent_contracts.testing import ctx
from agent_contracts.testing import workflow as wf
from agent_runtime.nodes import NodeRunner
from agent_spec import load_spec, load_spec_text
from agent_tools.authorising import AuthorisingToolExecutor
from agent_tools.builtin import WordCountTool
from agent_tools.executor import PlatformToolExecutor
from agent_tools.registry import InMemoryToolRegistry
from opentelemetry.sdk.trace import TracerProvider

F = "agent_contracts.testing.workflow"
ADAPTERS = ["langgraph", "agent_framework"]
PROVIDER = TracerProvider()  # spans stay in-process


def spec_text(adapter: str, body: str, tools: str = "") -> str:
    return f"""
spec_version: 1
name: wf-test
version: 0.1.0
owner: agent-platform
description: Step 12 test workflow.
risk_tier: low
autonomy: workflow
pattern: workflow
models: {{ default: local-qwen-fast }}
runtime: {{ adapter: {adapter}, durability: memory }}
budgets: {{ max_steps: 30, max_wall_seconds: 30, max_tokens: 10000, max_cost_aud: 1.0 }}
{tools}
graph:
{body}
"""


def tool_executor() -> tuple[AuthorisingToolExecutor, MemoryAuditSink]:
    reg = InMemoryToolRegistry()
    audit = MemoryAuditSink()
    return (
        AuthorisingToolExecutor(PlatformToolExecutor(reg), reg, RulesPolicyDecisionPoint(), audit),
        audit,
    )


async def _with_tools() -> tuple[AuthorisingToolExecutor, MemoryAuditSink]:
    ex, audit = tool_executor()
    await ex.registry.register(WordCountTool())
    return ex, audit


# ------------------------------------------------------------------ saga


SAGA = f"""
  entry: reserve
  nodes:
    - {{ id: reserve, type: function, fn: {F}.reserve, compensation: release }}
    - {{ id: charge, type: function, fn: {F}.charge, compensation: refund }}
    - {{ id: ship, type: function, fn: {F}.ship }}
    - {{ id: release, type: function, fn: {F}.release }}
    - {{ id: refund, type: function, fn: {F}.refund }}
  edges: ["reserve -> charge", "charge -> ship"]
"""


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_failed_step_triggers_compensation_newest_first(adapter: str) -> None:
    wf.SAGA_LOG.clear()
    result, _ = await run(load_spec_text(spec_text(adapter, SAGA)), {}, ctx(), PROVIDER)
    assert result.status is RunStatus.FAILED
    assert wf.SAGA_LOG == [
        "reserve",
        "charge",
        "ship",
        "refund (undoing charge)",
        "release (undoing reserve)",
    ]
    assert result.state.values["refund"] == "refunded"
    assert result.state.values["release"] == "released"
    assert "courier unavailable" in (result.state.error or "")
    assert "compensated: refund, release" in (result.state.error or "")


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_successful_run_compensates_nothing(adapter: str) -> None:
    wf.SAGA_LOG.clear()
    body = SAGA.replace('"charge -> ship"', '"charge -> done"').replace(
        f"{{ id: ship, type: function, fn: {F}.ship }}",
        f"{{ id: done, type: function, fn: {F}.start }}",
    )
    result, _ = await run(load_spec_text(spec_text(adapter, body)), {}, ctx(), PROVIDER)
    assert result.status is RunStatus.SUCCEEDED
    assert wf.SAGA_LOG == ["reserve", "charge"]


def test_compensation_steps_are_not_starts_or_unreachable() -> None:
    spec = load_spec_text(spec_text("langgraph", SAGA.replace("  entry: reserve\n", "")))
    from agent_compiler.compiler import compile_spec

    plan = compile_spec(spec)
    assert plan.entry == "reserve"
    assert set(plan.exits) == {"ship"}


# ------------------------------------------------------------------ parallel LLM + tool


PARALLEL = f"""
  entry: start
  nodes:
    - {{ id: start, type: function, fn: {F}.start }}
    - id: summary
      type: llm
      config: {{ template: "Summarise in one line: $input.text" }}
    - id: count
      type: tool
      tool: word_count
      config: {{ args: {{ text: "$input.text" }} }}
    - {{ id: both, type: join }}
    - {{ id: size, type: router, fn: {F}.long_or_short, routes: [long, short] }}
    - {{ id: long, type: function, fn: {F}.start }}
    - {{ id: short, type: function, fn: {F}.start }}
  edges:
    - "start -> summary"
    - "start -> count"
    - "summary -> both"
    - "count -> both"
    - "both -> size"
    - "size -> long: long"
    - "size -> short: short"
"""
TOOLS = "tools: [{ ref: local://platform/word_count }]"


class SlowModel(ModelClient):
    """Answers after a delay, to show the LLM node and the tool node overlap."""

    def __init__(self, delay: float = 0.4) -> None:
        self.delay = delay
        self.requests: list[ChatRequest] = []

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities()

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        await asyncio.sleep(self.delay)
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content="a short summary"), model=request.model
        )

    async def stream(self, request: ChatRequest) -> Any:  # pragma: no cover - not used
        raise NotImplementedError


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_llm_and_tool_nodes_run_in_parallel_and_route(adapter: str) -> None:
    tools, audit = await _with_tools()
    model = SlowModel()
    spec = load_spec_text(spec_text(adapter, PARALLEL, TOOLS))
    started = asyncio.get_running_loop().time()
    c = ctx()
    result, _ = await run(
        spec, {"text": "one two three four five"}, c, PROVIDER, model=model, tools=tools
    )
    assert result.status is RunStatus.SUCCEEDED, result.state.error
    values = result.state.values
    assert values["summary"] == "a short summary"
    assert values["count"]["words"] == 5
    assert "long" in values
    assert "short" not in values
    assert model.requests[0].messages[-1].content == (
        "Summarise in one line: one two three four five"
    )
    assert asyncio.get_running_loop().time() - started < 2
    (record,) = await audit.read()
    assert record.event.payload["tool"] == "word_count"
    assert record.event.payload["decision"] == "allow"


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_tool_node_uses_a_stable_idempotency_key(adapter: str) -> None:
    tools, _ = await _with_tools()
    seen: list[str | None] = []
    inner = tools.inner
    original = inner.execute

    async def spy(call: Any, run_ctx: Any) -> Any:
        seen.append(call.idempotency_key)
        return await original(call, run_ctx)

    inner.execute = spy  # type: ignore[method-assign]
    c = ctx()
    await run(
        load_spec_text(spec_text(adapter, PARALLEL, TOOLS)),
        {"text": "a b"},
        c,
        PROVIDER,
        model=SlowModel(0),
        tools=tools,
    )
    assert seen == [f"{c.run_id}:count"]


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_policy_denial_on_a_tool_node_fails_and_compensates(adapter: str) -> None:
    wf.SAGA_LOG.clear()
    tools, audit = await _with_tools()
    body = f"""
  entry: reserve
  nodes:
    - {{ id: reserve, type: function, fn: {F}.reserve, compensation: release }}
    - id: count
      type: tool
      tool: word_count
      config: {{ args: {{ text: "x", tenant_id: "another-tenant" }} }}
    - {{ id: release, type: function, fn: {F}.release }}
  edges: ["reserve -> count"]
"""
    result, _ = await run(
        load_spec_text(spec_text(adapter, body, TOOLS)), {}, ctx(), PROVIDER, tools=tools
    )
    assert result.status is RunStatus.FAILED
    assert "denied" in (result.state.error or "")
    assert wf.SAGA_LOG == ["reserve", "release (undoing reserve)"]
    assert (await audit.read())[0].event.payload["decision"] == "deny"


# ------------------------------------------------------------------ replay


ROUTED_BY_MODEL = f"""
  entry: classify
  nodes:
    - id: classify
      type: router
      routes: [approve, reject]
      config: {{ template: "Decide: $input.case" }}
    - {{ id: approve, type: function, fn: {F}.start }}
    - {{ id: reject, type: function, fn: {F}.start }}
  edges: ["classify -> approve: approve", "classify -> reject: reject"]
"""


class ExplodingModel(ScriptedModelClient):
    async def chat(self, request: ChatRequest) -> ChatResponse:
        raise AssertionError("a replay must not call the model")


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_replay_takes_the_same_path_without_calling_the_model(adapter: str) -> None:
    spec = load_spec_text(spec_text(adapter, ROUTED_BY_MODEL))
    model = ScriptedModelClient([Message(role=Role.ASSISTANT, content='{"route": "reject"}')])
    first_ctx = ctx()
    from agent_compiler.compiler import compile_spec
    from agent_compiler.run import executors_for, functions_for

    plan = compile_spec(spec)
    fns = functions_for(plan)
    recorder = NodeRunner(fns, executors_for(spec, plan, fns, model=model))
    first, _ = await run(spec, {"case": "c-1"}, first_ctx, PROVIDER, runner=recorder)
    journal = recorder.journal(first_ctx.run_id)

    replayer = NodeRunner(
        fns, executors_for(spec, plan, fns, model=ExplodingModel()), replay=journal
    )
    second, _ = await run(spec, {"case": "c-1"}, ctx(), PROVIDER, runner=replayer)
    assert first.status is second.status is RunStatus.SUCCEEDED
    assert "reject" in first.state.values
    assert first.state.values == second.state.values
    assert len(model.requests) == 1


# ------------------------------------------------------------------ sub-workflow and timeout


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_sub_workflow_runs_as_a_child_run(adapter: str, tmp_path: Path) -> None:
    child = spec_text(
        adapter,
        f"""
  nodes:
    - {{ id: greet, type: function, fn: {F}.hello }}
""",
    ).replace("name: wf-test", "name: wf-child")
    (tmp_path / "child.yaml").write_text(child)
    parent = spec_text(
        adapter,
        f"""
  entry: prepare
  nodes:
    - {{ id: prepare, type: function, fn: {F}.start }}
    - id: sub
      type: subgraph
      spec: child.yaml
      config: {{ inputs: {{ name: "$input.who" }} }}
  edges: ["prepare -> sub"]
""",
    )
    (tmp_path / "parent.yaml").write_text(parent)
    spec = load_spec(tmp_path / "parent.yaml")
    result, _ = await run(spec, {"who": "Ada"}, ctx(), PROVIDER, spec_path=tmp_path / "parent.yaml")
    assert result.status is RunStatus.SUCCEEDED, result.state.error
    assert result.output == {"greeting": "hello, Ada"}


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_node_timeout_takes_the_error_route(adapter: str) -> None:
    body = f"""
  entry: slow
  nodes:
    - {{ id: slow, type: function, fn: {F}.slow_half_second, timeout_seconds: 0.1 }}
    - {{ id: fallback, type: function, fn: {F}.start }}
  edges: ["slow -> fallback: on_error"]
"""
    result, _ = await run(load_spec_text(spec_text(adapter, body)), {}, ctx(), PROVIDER)
    assert result.status is RunStatus.SUCCEEDED
    assert result.state.values["slow"] == {"error": "timed out after 0.1s"}
    assert result.output == "A"
