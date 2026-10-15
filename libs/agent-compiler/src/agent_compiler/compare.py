"""Compare agent patterns on the same reference tasks (Step 14 "Done when").

Each pattern runs every case as a whole-spec agent with the same tools and model; the harness
meters what each run cost at the model-client and tool-executor boundary (model calls, tool
calls, input/output tokens, cost in AUD as reported by the gateway) and checks the answer
contains the expected value. Results are a table plus the metadata needed to quote them honestly:
model alias, dataset path and size, and the time of the run.

    uv run python -m agent_compiler.compare evals/reference/planning-tasks/cases.jsonl
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_contracts.interfaces import ModelClient, ToolExecutor
from agent_contracts.models import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    ModelCapabilities,
    RunContext,
    RunStatus,
    ToolCall,
    ToolResult,
)
from agent_spec import load_spec_text
from opentelemetry.sdk.trace import TracerProvider

from agent_compiler.run import run

PATTERNS = ("react", "plan_execute", "rewoo", "reflection", "evaluator_optimiser")


@dataclass
class Meter:
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_aud: float = 0.0


class MeteredModel(ModelClient):
    def __init__(self, inner: ModelClient, meter: Meter) -> None:
        self.inner, self.meter = inner, meter

    def capabilities(self, model: str) -> ModelCapabilities:
        return self.inner.capabilities(model)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        response = await self.inner.chat(request)
        self.meter.model_calls += 1
        self.meter.input_tokens += response.usage.input_tokens
        self.meter.output_tokens += response.usage.output_tokens
        self.meter.cost_aud += response.usage.cost_aud
        return response

    def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        return self.inner.stream(request)


class MeteredTools(ToolExecutor):
    def __init__(self, inner: ToolExecutor, meter: Meter) -> None:
        self.inner, self.meter = inner, meter
        self.registry = getattr(inner, "registry", None)

    async def execute(self, call: ToolCall, ctx: RunContext) -> ToolResult:
        self.meter.tool_calls += 1
        return await self.inner.execute(call, ctx)


@dataclass
class Row:
    pattern: str
    solved: int = 0
    stopped: int = 0
    failed: int = 0
    meter: Meter = field(default_factory=Meter)


def spec_for(pattern: str, max_steps: int = 16, adapter: str = "langgraph") -> str:
    return f"""
spec_version: 1
name: compare-{pattern.replace("_", "-")}
version: 0.1.0
owner: agent-platform
description: Pattern comparison on reference tasks.
risk_tier: low
autonomy: agent
pattern: {pattern}
models: {{ default: local-qwen-fast }}
runtime: {{ adapter: {adapter}, durability: memory }}
budgets: {{ max_steps: {max_steps}, max_wall_seconds: 300, max_tokens: 100000, max_cost_aud: 2.0 }}
tools:
  - ref: local://platform/calculator
  - ref: local://platform/word_count
"""


async def compare(
    cases: Sequence[dict[str, Any]],
    model_for: Callable[[dict[str, Any]], ModelClient],
    tools_for: Callable[[], Any],
    patterns: Sequence[str] = PATTERNS,
    adapter: str = "langgraph",
    provider: TracerProvider | None = None,
) -> list[Row]:
    rows = []
    for pattern in patterns:
        row = Row(pattern)
        spec = load_spec_text(spec_for(pattern, adapter=adapter))
        for case in cases:
            ctx = RunContext(tenant_id="eval", user_id="eval", agent_id=spec.name)
            result, _ = await run(
                spec,
                {"question": case["question"]},
                ctx,
                provider,
                model=MeteredModel(model_for(case), row.meter),
                tools=MeteredTools(await tools_for(), row.meter),
            )
            if result.status is RunStatus.STOPPED:
                row.stopped += 1
            elif result.status is not RunStatus.SUCCEEDED:
                row.failed += 1
            elif str(case["expect"]) in json.dumps(result.output, default=str):
                row.solved += 1
        rows.append(row)
    return rows


def table(rows: Sequence[Row], cases: int, model: str, dataset: str) -> str:
    head = (
        f"Dataset: {dataset} ({cases} tasks) · model: {model} · run at "
        f"{datetime.now(UTC).isoformat(timespec='seconds')}\n\n"
        "| pattern | solved | stopped | failed | model calls | tool calls | tokens in | tokens out | cost AUD |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
    )
    return head + "\n".join(
        f"| {r.pattern} | {r.solved}/{cases} | {r.stopped} | {r.failed} | {r.meter.model_calls} | "
        f"{r.meter.tool_calls} | {r.meter.input_tokens} | {r.meter.output_tokens} | "
        f"{r.meter.cost_aud:.4f} |"
        for r in rows
    )


async def _main(path: str, cases: list[dict[str, Any]]) -> int:
    from agent_contracts.dummies.observability import MemoryAuditSink
    from agent_runtime.models.gateway import GatewayModelClient
    from agent_tools.authorising import AuthorisingToolExecutor
    from agent_tools.builtin import CalculatorTool, WordCountTool
    from agent_tools.executor import PlatformToolExecutor
    from agent_tools.policy import OpaPolicyDecisionPoint
    from agent_tools.registry import InMemoryToolRegistry

    gateway = GatewayModelClient(spec_models={"default": "local-qwen-fast"})

    async def tools() -> AuthorisingToolExecutor:
        reg = InMemoryToolRegistry()
        for t in (CalculatorTool(), WordCountTool()):
            await reg.register(t)
        return AuthorisingToolExecutor(
            PlatformToolExecutor(reg), reg, OpaPolicyDecisionPoint(), MemoryAuditSink()
        )

    rows = await compare(cases, lambda _: gateway, tools)
    alias = gateway.resolve("default")[1]
    print(table(rows, len(cases), alias, path))
    return 0


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


if __name__ == "__main__":
    dataset = sys.argv[1] if len(sys.argv) > 1 else "evals/reference/planning-tasks/cases.jsonl"
    sys.exit(asyncio.run(_main(dataset, load_cases(dataset))))
