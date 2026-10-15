"""Does memory help on repeated tasks? (Step 20 "Done when").

Each case is two sessions of the same agent for the same user: in the first the user states a
fact or preference (*teach*), in a later one the agent is asked something that needs it (*ask*).
Every case runs twice, without memory and with it (recall, the remember tool, episodes, the
validating policy), and is scored on whether the answer contains the expected text, and on
cross-user leaks (an answer built from another user's memory).

Offline, the stand-in model stores the case's ``remember`` text when taught and answers with the
top memory it was given, so the result measures the platform's part (storing, scoping, recall)
rather than a model's use of memory. ``--model gateway`` uses the real executor model for both
sessions (Mac, VERIFY-ON-MAC).

    uv run python -m agent_memory.repeat_eval evals/golden/memory-repeat [--store pgvector]
        [--embedder hashing|gateway] [--model standin|gateway]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_contracts.dummies.embedder import HashingEmbedder
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.dummies.state import InMemoryMemoryStore
from agent_contracts.interfaces import Embedder, MemoryStore, ModelClient
from agent_contracts.models import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    Message,
    ModelCapabilities,
    NodeKind,
    PlanNode,
    Role,
    RunContext,
    ToolCall,
    Usage,
)
from agent_runtime.agent_loop import AgentLoopExecutor
from agent_tools.authorising import AuthorisingToolExecutor
from agent_tools.executor import PlatformToolExecutor
from agent_tools.registry import InMemoryToolRegistry

from agent_memory.policy import ValidatingMemoryPolicy
from agent_memory.service import MemoryService
from agent_memory.tools import RememberTool

TENANT = "eval-memory"
MODES = ("no memory", "memory")
PREFIX = re.compile(r"^Memory \([^)]*\): ")


def load_cases(directory: str | Path) -> list[dict[str, Any]]:
    path = Path(directory) / "cases.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


class StandInModel(ModelClient):
    """Teach: store the case's fact with the remember tool (when offered). Ask: answer with the
    top memory in the context, or say it does not know."""

    def __init__(self, fact: str | None = None) -> None:
        self.fact = fact

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(tool_calling=True)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        offered = {t.name for t in request.tools}
        called = any(m.role is Role.TOOL for m in request.messages)
        if self.fact and "remember" in offered and not called:
            call = ToolCall(name="remember", arguments={"content": self.fact})
            message = Message(role=Role.ASSISTANT, tool_calls=(call,))
        else:
            memories = [m.content for m in request.messages if m.name == "memory"]
            text = PREFIX.sub("", memories[0]) if memories and not self.fact else "I don't know."
            message = Message(role=Role.ASSISTANT, content="Noted." if self.fact else text)
        return ChatResponse(message=message, model=request.model, usage=Usage())

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        response = await self.chat(request)
        yield ChatChunk(
            delta=response.message.content,
            tool_calls=response.message.tool_calls,
            finish_reason="tool_calls" if response.message.tool_calls else "stop",
        )


@dataclass
class Score:
    case: str
    correct: bool
    leaked: bool
    answer: str


async def session(
    model: ModelClient, memory: MemoryService | None, ctx: RunContext, message: str
) -> str:
    reg = InMemoryToolRegistry()
    names = []
    if memory is not None:
        await reg.register(RememberTool(memory))
        names = ["remember"]
    tools = AuthorisingToolExecutor(
        PlatformToolExecutor(reg), reg, RulesPolicyDecisionPoint(), MemoryAuditSink()
    )
    node = PlanNode(
        id="assistant",
        kind=NodeKind.AGENT,
        config={"loop": "react", "tools": names, "model": "executor", "max_iterations": 4},
    )
    loop = AgentLoopExecutor(model, tools, reg, memory=memory)
    out = await loop(node, {"input": {"message": message}}, ctx)
    return str(out["answer"])


def ctx(user: str, run: str) -> RunContext:
    return RunContext(tenant_id=TENANT, user_id=user, agent_id="assistant", run_id=run)


async def run_mode(
    mode: str,
    cases: Sequence[dict[str, Any]],
    store: MemoryStore,
    model_factory: Any,
) -> list[Score]:
    memory = MemoryService(store, ValidatingMemoryPolicy()) if mode == "memory" else None
    for case in cases:
        await session(
            model_factory(case["remember"]),
            memory,
            ctx(case["user"], f"{case['id']}-teach"),
            case["teach"],
        )
    scores = []
    for case in cases:
        answer = await session(
            model_factory(None), memory, ctx(case["user"], f"{case['id']}-ask"), case["ask"]
        )
        others = [c["remember"] for c in cases if c["user"] != case["user"]]
        scores.append(
            Score(
                case=case["id"],
                correct=case["expect"].lower() in answer.lower(),
                leaked=any(o.lower() in answer.lower() for o in others),
                answer=answer,
            )
        )
    return scores


async def evaluate(
    directory: str | Path, store: str = "memory", embedder: str = "hashing", model: str = "standin"
) -> dict[str, Any]:
    cases = load_cases(directory)
    emb: Embedder = HashingEmbedder()
    if embedder == "gateway":
        from agent_runtime.models.embeddings import GatewayEmbedder

        emb = GatewayEmbedder()
    if model == "gateway":
        from agent_runtime.models.gateway import GatewayModelClient

        gateway = GatewayModelClient()

        def factory(fact: str | None) -> ModelClient:
            return gateway
    else:

        def factory(fact: str | None) -> ModelClient:
            return StandInModel(fact)

    report: dict[str, Any] = {
        "dataset": str(directory),
        "cases": len(cases),
        "store": store,
        "embedder": embedder,
        "model": model,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "modes": {},
    }
    for mode in MODES:
        backend = await make_store(store, emb)
        scores = await run_mode(mode, cases, backend, factory)
        for user in {c["user"] for c in cases}:
            await backend.erase_subject(TENANT, user)
        report["modes"][mode] = {
            "correct": sum(s.correct for s in scores),
            "leaks": sum(s.leaked for s in scores),
            "answers": {s.case: s.answer for s in scores},
        }
    return report


async def make_store(name: str, embedder: Embedder) -> MemoryStore:
    if name == "memory":
        return InMemoryMemoryStore()
    if name == "pgvector":
        from agent_memory.pgvector import PgVectorMemoryStore

        dsn = os.environ.get("AGENT_DB_DSN")
        if not dsn:
            raise SystemExit("--store pgvector needs AGENT_DB_DSN")
        return PgVectorMemoryStore(dsn, embedder)
    raise SystemExit(f"unknown store {name!r} (memory, pgvector)")


def render(report: dict[str, Any]) -> str:
    n = report["cases"]
    lines = [
        f"Repeat-task memory eval · {report['dataset']} · {n} cases · store {report['store']} · "
        f"embedder {report['embedder']} · model {report['model']} · {report['at']}",
        "",
        "| Mode | Correct | Cross-user leaks |",
        "|---|---|---|",
    ]
    for mode, r in report["modes"].items():
        lines.append(f"| {mode} | {r['correct']}/{n} | {r['leaks']} |")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument("dataset", nargs="?", default="evals/golden/memory-repeat")
    p.add_argument("--store", default="memory", choices=["memory", "pgvector"])
    p.add_argument("--embedder", default="hashing", choices=["hashing", "gateway"])
    p.add_argument("--model", default="standin", choices=["standin", "gateway"])
    p.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = p.parse_args(argv)
    report = asyncio.run(evaluate(args.dataset, args.store, args.embedder, args.model))
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
