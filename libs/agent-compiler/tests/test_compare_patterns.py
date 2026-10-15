"""Step 14: every planning/reflection pattern runs the reference tasks from a spec, on both
runtime adapters, and the comparison harness meters each run.

The model here is a deterministic stand-in that replays each case's scripted trajectory. It
proves the plumbing (spec → pattern → strategy → tools under policy → metered row); the counts it
produces describe the script, not any model, and are never reported. Real numbers come from
``just compare-patterns`` against Project 0's gateway (VERIFY-ON-MAC).
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from agent_compiler.compare import PATTERNS, Meter, compare, table
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.models import ChatRequest, ChatResponse, Message, Role, ToolCall
from agent_tools.authorising import AuthorisingToolExecutor
from agent_tools.builtin import CalculatorTool, WordCountTool
from agent_tools.executor import PlatformToolExecutor
from agent_tools.registry import InMemoryToolRegistry
from opentelemetry.sdk.trace import TracerProvider

ROOT = Path(__file__).resolve().parents[3]
DATASET = ROOT / "evals/reference/planning-tasks/cases.jsonl"
CASES = [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]
_STEP = re.compile(r"\.step(\d+)$")


class ReferenceStandIn(ScriptedModelClient):
    """Answers by request purpose: plans with the case's plan, passes critiques, replays the
    case's ReAct calls turn by turn (or the matching call inside a plan step), and answers with
    the expected value once the tools have run."""

    def __init__(self, case: dict[str, Any]) -> None:
        super().__init__()
        self.case = case

    def _say(self, content: str) -> ChatResponse:
        return ChatResponse(message=Message(role=Role.ASSISTANT, content=content), model="stand-in")

    def _call(self, index: int) -> ChatResponse:
        call = self.case["react"][index]
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=(ToolCall(name=call["name"], arguments=call["arguments"]),),
            ),
            model="stand-in",
        )

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        assert any(self.case["question"] in m.content for m in request.messages), (
            "every prompt carries the task"
        )
        purpose = request.metadata.get("purpose")
        answer = f"The answer is {self.case['expect']}."
        if purpose == "plan":
            return self._say(json.dumps({"steps": self.case["plan"]}))
        if purpose == "critique":
            return self._say(json.dumps({"score": 0.9, "passed": True, "feedback": "correct"}))
        if purpose in {"solve", "synthesise", "draft"}:
            return self._say(answer)
        done = sum(m.role is Role.TOOL for m in request.messages)
        step = _STEP.search(str(request.metadata.get("node_id", "")))
        if step:  # one plan step: its matching call, then report the observation
            return self._say(request.messages[-1].content) if done else self._call(int(step[1]) - 1)
        return self._call(done) if done < len(self.case["react"]) else self._say(answer)


async def tools() -> AuthorisingToolExecutor:
    reg = InMemoryToolRegistry()
    for t in (CalculatorTool(), WordCountTool()):
        await reg.register(t)
    return AuthorisingToolExecutor(
        PlatformToolExecutor(reg), reg, RulesPolicyDecisionPoint(), MemoryAuditSink()
    )


@pytest.mark.parametrize("adapter", ["langgraph", "agent_framework"])
async def test_every_pattern_runs_the_reference_tasks(adapter: str) -> None:
    rows = await compare(
        CASES, ReferenceStandIn, tools, PATTERNS, adapter=adapter, provider=TracerProvider()
    )
    assert [r.pattern for r in rows] == list(PATTERNS)
    for row in rows:
        assert (row.solved, row.stopped, row.failed) == (len(CASES), 0, 0), row
        assert row.meter.model_calls > 0, row
    by = {r.pattern: r.meter for r in rows}
    tool_calls = sum(len(c["react"]) for c in CASES)
    for pattern in ("react", "plan_execute", "rewoo"):
        assert by[pattern].tool_calls == tool_calls, pattern
    # ReWOO's shape, not a result: one plan call and one solve call per task.
    assert by["rewoo"].model_calls == 2 * len(CASES)


def test_table_states_dataset_size_and_model() -> None:
    from agent_compiler.compare import Row

    out = table(
        [Row("react", solved=2, meter=Meter(model_calls=5))], 3, "local-qwen-fast", "x.jsonl"
    )
    assert "x.jsonl (3 tasks)" in out
    assert "model: local-qwen-fast" in out
    assert "| react | 2/3 |" in out
