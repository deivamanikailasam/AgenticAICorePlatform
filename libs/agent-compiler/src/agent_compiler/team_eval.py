"""Team patterns eval (Step 23 "Done when"): each reference spec on its golden tasks, and each
pattern pushed by agents that never end on their own.

Normal cases score whether the answer contains the expected text. Adversarial cases (handoff
ping-pong, a critic that never approves, too many items, a judge that never decides, a budget
far too small) pass only when the run stops at its limit, within the number of agent calls that
limit allows, without claiming success.

Agents are rule-based stand-ins (``respond``): the numbers show routing, termination and budget
behaviour, not real collaboration quality.

    uv run python -m agent_compiler.team_eval evals/golden/teams
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.interfaces import ModelClient
from agent_contracts.models import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    ExecutablePlan,
    Message,
    ModelCapabilities,
    Role,
    RunContext,
    RunStatus,
    Usage,
)
from agent_spec import AgentSpec, load_spec, load_spec_text
from agent_tools.authorising import AuthorisingToolExecutor, delegation_authoriser
from agent_tools.executor import PlatformToolExecutor
from agent_tools.registry import InMemoryToolRegistry
from opentelemetry.sdk.trace import TracerProvider

from agent_compiler.run import run

ROOT = Path(__file__).resolve().parents[4]
COST_PER_CALL = 0.001  # AUD, so budget limits bite in the eval


def respond(agent: str, prompt: str) -> dict[str, Any]:
    """Deterministic stand-in agents for the reference specs."""
    p = prompt.lower()
    role = agent.rsplit("-", 1)[-1]
    if role == "triage":
        if any(w in p for w in ("refund", "invoice", "charge")):
            return {"handoff_to": "billing", "note": "billing question"}
        if any(w in p for w in ("login", "error", "password")):
            return {"handoff_to": "tech", "note": "technical issue"}
        return {"answer": "Our opening hours are 9 to 5, Monday to Friday."}
    if role == "billing":
        return {"answer": "Refund approved and processed."}
    if role == "tech":
        return {"answer": "Password reset link sent; please try again."}
    if role == "writer":
        return {
            "message": "Draft v2: a kettle that boils fast. With a closing line."
            if "closing line" in p
            else "Draft v1: a kettle that boils fast."
        }
    if role == "critic":
        return {
            "message": "Good now. TERMINATE" if "draft v2" in p else "Please add a closing line."
        }
    if role == "mapper":
        doc = prompt.split("one sentence:", 1)[-1].strip()
        return {"message": f"Summary: {doc.split('.')[0]}."}
    if role == "reducer":
        return {"message": f"Digest of {p.count('summary:')} documents."}
    if role in ("pro", "con"):
        return {"argument": f"{role} argument"}
    if role == "judge":
        settled = p.count("argument") >= 4 or "final round: true" in p
        return (
            {"done": settled, "answer": "The proposal passes on balance."}
            if settled
            else {"done": False}
        )
    return {"message": "nothing to add"}


def stubborn(agent: str, prompt: str) -> dict[str, Any]:
    """Agents that never end on their own."""
    role = agent.rsplit("-", 1)[-1]
    if role in ("triage", "billing", "tech"):
        return {"handoff_to": "tech" if role != "tech" else "billing", "note": "not mine"}
    if role == "critic":
        return {"message": "Not yet. Try again."}
    if role == "judge":
        return {"done": False}
    return respond(agent, prompt)


class StandIn(ModelClient):
    def __init__(self, agent: str, policy: Callable[[str, str], dict[str, Any]]) -> None:
        self.agent, self.policy = agent, policy
        self.calls = 0

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(tool_calling=True, structured_output=True)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.calls += 1
        prompt = next(m.content for m in reversed(request.messages) if m.role is Role.USER)
        body = json.dumps(self.policy(self.agent, prompt))
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=body),
            model=request.model,
            usage=Usage(input_tokens=50, output_tokens=10, cost_aud=COST_PER_CALL),
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        response = await self.chat(request)
        yield ChatChunk(delta=response.message.content, finish_reason="stop")


class Workers:
    """Each agent gets its own (empty) tool registry and grants, and its own stand-in model."""

    def __init__(self, policy: Callable[[str, str], dict[str, Any]]) -> None:
        self.policy = policy
        self.audit = MemoryAuditSink()
        self.models: list[StandIn] = []

    async def __call__(self, spec: AgentSpec, plan: ExecutablePlan) -> tuple[Any, Any]:
        reg = InMemoryToolRegistry()
        tools = AuthorisingToolExecutor(
            PlatformToolExecutor(reg), reg, RulesPolicyDecisionPoint(), self.audit, grants={}
        )
        model = StandIn(spec.name, self.policy)
        self.models.append(model)
        return model, tools

    @property
    def calls(self) -> int:
        return sum(m.calls for m in self.models)


@dataclass
class Outcome:
    case: str
    spec: str
    passed: bool
    status: str
    agent_calls: int
    cost_aud: float
    detail: str


def output_of(result: Any) -> dict[str, Any]:
    out = result.output if isinstance(result.output, dict) else {}
    return dict(out.get("partial") or out)


async def run_case(
    spec: AgentSpec,
    path: Path,
    inputs: dict[str, Any],
    policy: Callable[[str, str], dict[str, Any]],
) -> tuple[Any, Workers]:
    workers = Workers(policy)
    ctx = RunContext(tenant_id="eval", user_id="u1", agent_id=spec.name)
    result, _ = await run(
        spec,
        inputs,
        ctx,
        TracerProvider(),  # spans stay in process: the eval needs no collector
        model=StandIn(spec.name, policy),
        spec_path=path,
        workers=workers,
        authorise=delegation_authoriser(RulesPolicyDecisionPoint(), workers.audit),
    )
    return result, workers


ADVERSARIAL: list[dict[str, Any]] = [
    {
        "id": "handoff-ping-pong",
        "spec": "support-handoff",
        "input": {"task": "refund please"},
        "limit": 4,
    },
    {
        "id": "chat-never-done",
        "spec": "review-chat",
        "input": {"task": "Write a blurb"},
        "limit": 6,
    },
    {
        "id": "too-many-items",
        "spec": "batch-summary",
        "input": {"task": "digest", "documents": ["x."] * 60},
        "limit": 0,
    },
    {
        "id": "judge-never-decides",
        "spec": "policy-debate",
        "input": {"task": "Adopt a policy"},
        "limit": 9,
    },
]


async def evaluate(directory: str | Path) -> dict[str, Any]:
    cases = [
        json.loads(x)
        for x in (Path(directory) / "cases.jsonl").read_text().splitlines()
        if x.strip()
    ]
    outcomes: list[Outcome] = []
    for case in cases:
        path = ROOT / "agents" / case["spec"] / "agent.yaml"
        result, workers = await run_case(load_spec(path), path, case["input"], respond)
        out = output_of(result)
        answer = str(out.get("answer", ""))
        ok = result.status is RunStatus.SUCCEEDED and case["expect"].lower() in answer.lower()
        outcomes.append(
            Outcome(
                case["id"],
                case["spec"],
                ok,
                result.status.value,
                workers.calls,
                sum(out.get("cost_by_agent", {}).values()),
                answer[:80],
            )
        )
    for case in ADVERSARIAL:
        path = ROOT / "agents" / case["spec"] / "agent.yaml"
        result, workers = await run_case(load_spec(path), path, case["input"], stubborn)
        out = output_of(result)
        ok = result.status is not RunStatus.SUCCEEDED and workers.calls <= case["limit"]
        detail = str(out.get("stop_reason") or result.state.error or "")
        outcomes.append(
            Outcome(
                case["id"],
                case["spec"],
                ok,
                result.status.value,
                workers.calls,
                sum(out.get("cost_by_agent", {}).values()),
                detail[:80],
            )
        )
    for name in ("support-handoff", "review-chat", "batch-summary", "policy-debate"):
        path = ROOT / "agents" / name / "agent.yaml"
        tight = load_spec_text(
            path.read_text().replace("max_cost_aud: 0.30", "max_cost_aud: 0.002")
        )
        inputs = next(c["input"] for c in cases if c["spec"] == name)
        result, workers = await run_case(tight, path, inputs, respond)
        out = output_of(result)
        spent = sum(out.get("cost_by_agent", {}).values())
        stopped = result.status is not RunStatus.SUCCEEDED or spent <= 0.002 + 4 * COST_PER_CALL
        detail = str(out.get("stop_reason") or "finished within budget")
        outcomes.append(
            Outcome(
                f"budget-{name}",
                name,
                stopped,
                result.status.value,
                workers.calls,
                spent,
                detail[:80],
            )
        )
    return {
        "dataset": str(directory),
        "cases": len(cases),
        "adversarial": len(ADVERSARIAL) + 4,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "outcomes": [o.__dict__ for o in outcomes],
    }


def render(report: dict[str, Any]) -> str:
    rows = report["outcomes"]
    passed = sum(r["passed"] for r in rows)
    lines = [
        f"Team patterns eval · {report['dataset']} · {report['cases']} tasks + {report['adversarial']} "
        f"limit checks · stand-in agents · {report['at']} · {passed}/{len(rows)} passed",
        "",
        "| Case | Spec | Passed | Status | Agent calls | Cost (AUD) | Answer / stop reason |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        detail = re.sub(r"\|", "/", r["detail"])
        lines.append(
            f"| {r['case']} | {r['spec']} | {'yes' if r['passed'] else 'NO'} | {r['status']} | "
            f"{r['agent_calls']} | {r['cost_aud']:.3f} | {detail} |"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Team patterns eval")
    p.add_argument("dataset", nargs="?", default="evals/golden/teams")
    args = p.parse_args(argv)
    report = asyncio.run(evaluate(args.dataset))
    print(render(report))
    return 0 if all(r["passed"] for r in report["outcomes"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
