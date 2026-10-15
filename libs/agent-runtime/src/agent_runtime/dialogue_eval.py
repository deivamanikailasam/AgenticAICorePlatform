"""Long dialogues within the context window (Step 21 "Done when").

Each dialogue is expanded to 200 user turns (small talk, facts, corrections, questions) and
replayed once per context strategy: ``unlimited`` (the whole transcript every time: the
baseline), ``rolling_summary``, ``trim`` and ``offload_s3``. At every question the model is
called with what the strategy shows it; the report gives answers correct, the largest prompt
(estimated tokens) against the window, and how many calls went over it.

Offline the stand-in model answers with the newest sentence in its context containing the
question's cue word, and summaries are extractive, so the numbers measure what each strategy
keeps, not how well a real model reads it; ``--model gateway`` uses the real model and the
LLM summariser (Mac, VERIFY-ON-MAC).

    uv run python -m agent_runtime.dialogue_eval evals/golden/long-dialogue [--window 8192]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_contracts.interfaces import ModelClient
from agent_contracts.models import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    Message,
    ModelCapabilities,
    Role,
    RunContext,
    Trust,
    Usage,
)

from agent_runtime.context import (
    ContextWindow,
    ExtractiveSummariser,
    LlmSummariser,
    OffloadContextManager,
    RollingSummaryContextManager,
    TrimContextManager,
    estimate,
)

STRATEGIES = ("unlimited", "rolling_summary", "trim", "offload_s3")
FILLER = (
    "Tell me more about {topic}, please.",
    "What else is interesting about {topic}?",
    "That sounds nice. Go on about {topic}.",
    "Can you share another thought on {topic}?",
    "Interesting. What would you add about {topic}?",
)
REPLY = "Here is another thought about {topic}: it can be enjoyable in many ways, " + "really " * 12


def load(directory: str | Path) -> list[dict[str, Any]]:
    path = Path(directory) / "dialogues.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


class StandIn(ModelClient):
    """Answers a question with the newest sentence in its context that has the cue word."""

    def __init__(self, window: int) -> None:
        self.window = window
        self.cue = ""

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(context_window=self.window)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        answer = "I don't know."
        for m in reversed(request.messages[:-1]):
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n", m.content) if s.strip()]
            hit = next(
                (s for s in reversed(sentences) if self.cue in s.lower() and not s.endswith("?")),
                None,
            )
            if hit:
                answer = hit
                break
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=answer), model=request.model, usage=Usage()
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        response = await self.chat(request)
        yield ChatChunk(delta=response.message.content, finish_reason="stop")


class Archive:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, content_type: str = "application/json") -> str:
        self.objects[key] = data
        return f"memory://{key}"


def window_for(strategy: str, model: ModelClient, real: bool) -> ContextWindow | None:
    if strategy == "unlimited":
        return None
    summariser: Any = LlmSummariser(model) if real else ExtractiveSummariser()
    if strategy == "rolling_summary":
        return ContextWindow(RollingSummaryContextManager(summariser=summariser))
    if strategy == "trim":
        return ContextWindow(TrimContextManager())
    return ContextWindow(OffloadContextManager(Archive()))


async def replay(
    dialogue: dict[str, Any], strategy: str, model: ModelClient, window: int, real: bool
) -> dict[str, Any]:
    ctx = RunContext(
        tenant_id="eval", user_id="u1", agent_id="assistant", run_id=f"{dialogue['id']}-{strategy}"
    )
    policy = window_for(strategy, model, real)
    topic = dialogue["topic"]
    transcript = [Message(role=Role.SYSTEM, content="You are a helpful assistant. Answer briefly.")]
    correct, largest, over = 0, 0, 0
    budget = policy.budget(window, ()) if policy else window
    for turn in range(int(dialogue["turns"])):
        key = str(turn)
        probe = dialogue["probes"].get(key)
        text = (
            probe["ask"]
            if probe
            else dialogue["facts"].get(key) or FILLER[turn % len(FILLER)].format(topic=topic)
        )
        transcript.append(Message(role=Role.USER, content=text, trust=Trust.USER))
        if not probe:
            transcript.append(Message(role=Role.ASSISTANT, content=REPLY.format(topic=topic)))
            continue
        shown = await policy.messages(transcript, ctx, budget) if policy else list(transcript)
        size = sum(estimate(m) for m in shown)
        largest, over = max(largest, size), over + (size > window)
        if isinstance(model, StandIn):
            model.cue = probe["cue"]
        reply = (await model.chat(ChatRequest(model="executor", messages=tuple(shown)))).message
        correct += probe["expect"].lower() in reply.content.lower()
        transcript.append(Message(role=Role.ASSISTANT, content=reply.content))
    return {"correct": correct, "probes": len(dialogue["probes"]), "largest": largest, "over": over}


async def evaluate(
    directory: str | Path, window: int = 8192, model: str = "standin"
) -> dict[str, Any]:
    dialogues = load(directory)
    real = model == "gateway"
    report: dict[str, Any] = {
        "dataset": str(directory),
        "dialogues": len(dialogues),
        "turns": sum(int(d["turns"]) for d in dialogues),
        "probes": sum(len(d["probes"]) for d in dialogues),
        "window": window,
        "model": model,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "strategies": {},
    }
    for strategy in STRATEGIES:
        client: ModelClient
        if real:
            from agent_runtime.models.gateway import GatewayModelClient

            client = GatewayModelClient()
        else:
            client = StandIn(window)
        rows = [await replay(d, strategy, client, window, real) for d in dialogues]
        report["strategies"][strategy] = {
            "correct": sum(r["correct"] for r in rows),
            "largest": max(r["largest"] for r in rows),
            "over": sum(r["over"] for r in rows),
        }
    return report


def render(report: dict[str, Any]) -> str:
    lines = [
        f"Long-dialogue eval · {report['dataset']} · {report['dialogues']} dialogues, "
        f"{report['turns']} user turns, {report['probes']} questions · window {report['window']} "
        f"tokens · model {report['model']} · {report['at']}",
        "",
        "| Strategy | Correct | Largest prompt (est. tokens) | Calls over the window |",
        "|---|---|---|---|",
    ]
    for name, r in report["strategies"].items():
        lines.append(
            f"| {name} | {r['correct']}/{report['probes']} | {r['largest']} | {r['over']} |"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Long dialogues within the context window")
    p.add_argument("dataset", nargs="?", default="evals/golden/long-dialogue")
    p.add_argument("--window", type=int, default=8192)
    p.add_argument("--model", default="standin", choices=["standin", "gateway"])
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    report = asyncio.run(evaluate(args.dataset, args.window, args.model))
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
