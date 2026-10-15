"""Planning and reflection strategies (Step 14) with scripted models and real platform tools."""

import json
from typing import Any

import pytest
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.interfaces import StopRun
from agent_contracts.models import Message, NodeKind, PlanNode, Role, ToolCall
from agent_contracts.testing import ctx
from agent_runtime.agent_loop import AgentLoopExecutor
from agent_runtime.reasoning import LlmCritic, LlmPlanner
from agent_runtime.strategies import (
    EvaluatorOptimiserStrategy,
    PlanExecuteStrategy,
    ReflectionStrategy,
    RewooStrategy,
    substitute,
)
from agent_tools.authorising import AuthorisingToolExecutor
from agent_tools.builtin import CalculatorTool, WordCountTool
from agent_tools.executor import PlatformToolExecutor
from agent_tools.registry import InMemoryToolRegistry


def say(obj: Any) -> Message:
    return Message(role=Role.ASSISTANT, content=obj if isinstance(obj, str) else json.dumps(obj))


def call(name: str, **args: Any) -> Message:
    return Message(role=Role.ASSISTANT, tool_calls=(ToolCall(name=name, arguments=args),))


def test_evidence_substitution() -> None:
    evidence = {"E1": {"words": 4}, "E2": "hello"}
    assert substitute("#E1.words * 7", evidence) == "4 * 7"
    assert substitute("#E1", evidence) == {"words": 4}
    assert substitute({"text": "#E2 world", "n": ["#E1.words"]}, evidence) == {
        "text": "hello world",
        "n": [4],
    }


async def _parts(model: ScriptedModelClient) -> dict[str, Any]:
    reg = InMemoryToolRegistry()
    for t in (CalculatorTool(), WordCountTool()):
        await reg.register(t)
    audit = MemoryAuditSink()
    tools = AuthorisingToolExecutor(
        PlatformToolExecutor(reg), reg, RulesPolicyDecisionPoint(), audit
    )
    return {
        "model": model,
        "tools": tools,
        "registry": reg,
        "planner": lambda m: LlmPlanner(m),
        "critic": lambda m, role: LlmCritic(m, role),
        "react": lambda m: AgentLoopExecutor(m, tools, reg),
        "_audit": audit,
    }


def node(**config: Any) -> PlanNode:
    return PlanNode(
        id="agent.loop",
        kind=NodeKind.AGENT,
        config={
            "loop": config.pop("loop"),
            "tools": config.pop("tools", ["calculator", "word_count"]),
            "model": "default",
            "max_iterations": config.pop("max_iterations", 12),
            "config": {},
            **config,
        },
    )


TASK = {"input": {"question": "Count the words in 'a b c d' and multiply the count by 7."}}


async def test_rewoo_plans_once_runs_tools_without_the_model_and_solves_once() -> None:
    model = ScriptedModelClient(
        [
            say(
                {
                    "steps": [
                        {
                            "id": "E1",
                            "description": "count",
                            "tool": "word_count",
                            "arguments": {"text": "a b c d"},
                        },
                        {
                            "id": "E2",
                            "description": "multiply",
                            "tool": "calculator",
                            "arguments": {"expression": "#E1.words * 7"},
                            "depends_on": ["E1"],
                        },
                    ]
                }
            ),
            say("28"),
        ]
    )
    parts = await _parts(model)
    audit = parts.pop("_audit")
    out = await RewooStrategy(**parts)(node(loop="rewoo"), TASK, ctx())
    assert out["answer"] == "28"
    assert out["evidence"]["E2"] == {"result": 28.0}
    assert len(model.requests) == 2, "one plan call, one solve call"
    assert [r.event.payload["tool"] for r in await audit.read()] == ["word_count", "calculator"]
    assert "data, not instructions" in model.requests[1].messages[-1].content


async def test_plan_execute_runs_steps_and_replans_on_failure() -> None:
    model = ScriptedModelClient(
        [
            say({"steps": [{"id": "E1", "description": "count words", "tool": "word_count"}]}),
            call("nope"),
            call("nope"),
            call("nope"),  # step 1 fails: malformed beyond repairs
            say(
                {
                    "steps": [
                        {"id": "E1", "description": "count words", "tool": "word_count"},
                        {"id": "E2", "description": "multiply", "tool": "calculator"},
                    ]
                }
            ),
            call("word_count", text="a b c d"),
            say("4 words"),
            call("calculator", expression="4 * 7"),
            say("28"),
            say("The answer is 28."),
        ]
    )
    parts = await _parts(model)
    parts.pop("_audit")
    out = await PlanExecuteStrategy(**parts)(
        node(loop="plan_execute", max_iterations=20), TASK, ctx()
    )
    assert out["replans"] == 1
    assert out["results"] == {"E1": "4 words", "E2": "28"}
    assert out["answer"] == "The answer is 28."
    assert len(out["plans"]) == 2


async def test_reflection_revises_until_the_self_critique_passes() -> None:
    model = ScriptedModelClient(
        [
            say("a draft"),
            say({"score": 0.3, "passed": False, "feedback": "show the arithmetic"}),
            say("4 words x 7 = 28"),
            say({"score": 0.9, "passed": True, "feedback": "good"}),
        ]
    )
    parts = await _parts(model)
    parts.pop("_audit")
    out = await ReflectionStrategy(**parts)(
        node(loop="reflection", tools=[], max_rounds=2), TASK, ctx()
    )
    assert out["answer"] == "4 words x 7 = 28"
    assert out["rounds"] == 1
    critique_requests = [r for r in model.requests if r.metadata.get("purpose") == "critique"]
    assert {r.model for r in critique_requests} == {"default"}, "self-critique uses the same role"
    assert "show the arithmetic" in model.requests[2].messages[-1].content


async def test_evaluator_optimiser_keeps_the_best_candidate_within_rounds() -> None:
    model = ScriptedModelClient(
        [
            say("first"),
            say({"score": 0.4, "passed": False, "feedback": "more detail"}),
            say("second"),
            say({"score": 0.8, "passed": False, "feedback": "almost"}),
        ]
    )
    parts = await _parts(model)
    parts.pop("_audit")
    out = await EvaluatorOptimiserStrategy(**parts)(
        node(loop="evaluator_optimiser", tools=[], max_rounds=1), TASK, ctx()
    )
    assert out["answer"] == "second"
    assert out["rounds"] == 1
    assert {r.model for r in model.requests if r.metadata.get("purpose") == "critique"} == {"judge"}


async def test_model_call_cap_covers_planner_critic_and_sub_steps() -> None:
    model = ScriptedModelClient(
        [say("draft")] + [say({"score": 0.1, "passed": False, "feedback": "no"}), say("again")] * 5
    )
    parts = await _parts(model)
    parts.pop("_audit")
    with pytest.raises(StopRun, match=r"max_steps \(3\)"):
        await ReflectionStrategy(**parts)(
            node(loop="reflection", tools=[], max_rounds=5, max_iterations=3), TASK, ctx()
        )
    assert len(model.requests) == 3


async def test_strategies_refuse_tools_that_need_approval() -> None:
    parts = await _parts(ScriptedModelClient())
    parts.pop("_audit")
    with pytest.raises(ValueError, match="pattern 'react'"):
        await RewooStrategy(**parts)(node(loop="rewoo", approval_tools=["calculator"]), TASK, ctx())
