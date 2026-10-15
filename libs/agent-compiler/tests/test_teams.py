"""Handoff, group chat, map-reduce and debate (Step 23): reference specs, the eval, and the
limits every pattern must stop at."""

import asyncio
import json
from pathlib import Path
from typing import Any

from agent_compiler.run import run
from agent_compiler.team_eval import ROOT, StandIn, Workers, evaluate, output_of, respond, run_case
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.models import (
    ChatRequest,
    ChatResponse,
    Message,
    PolicyDecision,
    PolicyEffect,
    Role,
    RunContext,
    RunStatus,
)
from agent_spec import load_spec, load_spec_text
from opentelemetry.sdk.trace import TracerProvider


def spec_path(name: str) -> Path:
    return ROOT / "agents" / name / "agent.yaml"


async def test_every_reference_spec_passes_and_no_pattern_runs_past_its_limits() -> None:
    report = await evaluate(ROOT / "evals/golden/teams")
    failed = [r for r in report["outcomes"] if not r["passed"]]
    assert failed == []
    assert report["cases"] == 7
    assert report["adversarial"] == 8


async def test_a_handoff_to_an_agent_that_is_not_allowed_stops_the_run() -> None:
    path = spec_path("support-handoff")
    spec = load_spec_text(path.read_text().replace("triage: [billing, tech]", "triage: [tech]"))
    result, _ = await run_case(spec, path, {"task": "refund for invoice 9"}, respond)
    assert result.status is RunStatus.STOPPED
    out = output_of(result)
    assert out["rounds"][0]["outcome"] == "refused: triage may not hand off to billing"


async def test_policy_can_deny_a_hand_over_inside_a_pattern() -> None:
    path = spec_path("support-handoff")
    spec = load_spec(path)
    workers = Workers(respond)

    async def no_billing(worker: str, team: Any, ctx: Any) -> PolicyDecision:
        effect = PolicyEffect.DENY if worker == "billing" else PolicyEffect.ALLOW
        return PolicyDecision(effect=effect, reasons=("billing is switched off",))

    result, _ = await run(
        spec,
        {"task": "refund for invoice 9"},
        RunContext(tenant_id="t", user_id="u", agent_id=spec.name),
        TracerProvider(),
        model=StandIn(spec.name, respond),
        spec_path=path,
        workers=workers,
        authorise=no_billing,
    )
    assert result.status is RunStatus.STOPPED
    assert output_of(result)["rounds"][1]["outcome"] == "denied by policy: billing is switched off"


class Slow(StandIn):
    live = 0
    peak = 0

    async def chat(self, request: ChatRequest) -> ChatResponse:
        Slow.live += 1
        Slow.peak = max(Slow.peak, Slow.live)
        await asyncio.sleep(0.02)
        Slow.live -= 1
        return await super().chat(request)


async def test_map_reduce_never_runs_more_mappers_at_once_than_its_concurrency() -> None:
    path = spec_path("batch-summary")
    spec = load_spec_text(path.read_text().replace("concurrency: 4", "concurrency: 3"))

    class SlowWorkers(Workers):
        async def __call__(self, spec: Any, plan: Any) -> tuple[Any, Any]:
            _, tools = await super().__call__(spec, plan)
            model = Slow(spec.name, self.policy)
            self.models.append(model)
            return model, tools

    docs = [f"Document {i} says something." for i in range(12)]
    result, _ = await run(
        spec,
        {"task": "digest", "documents": docs},
        RunContext(tenant_id="t", user_id="u", agent_id=spec.name),
        TracerProvider(),
        model=StandIn(spec.name, respond),
        spec_path=path,
        workers=SlowWorkers(respond),
    )
    assert result.status is RunStatus.SUCCEEDED
    assert output_of(result)["answer"] == "Digest of 12 documents."
    assert Slow.peak == 3


async def test_the_llm_speaker_selector_sees_messages_as_data_and_picks_from_the_team() -> None:
    path = spec_path("review-chat")
    spec = load_spec_text(path.read_text().replace("speaker: round_robin", "speaker: llm"))
    selector = ScriptedModelClient(
        [
            Message(role=Role.ASSISTANT, content=json.dumps({"next": n}))
            for n in ("writer", "critic", "writer", "critic")
        ]
    )
    result, _ = await run(
        spec,
        {"task": "Write a blurb"},
        RunContext(tenant_id="t", user_id="u", agent_id=spec.name),
        TracerProvider(),
        model=selector,
        spec_path=path,
        workers=Workers(respond),
    )
    assert result.status is RunStatus.SUCCEEDED
    assert output_of(result)["answer"].startswith("Draft v2")
    second = selector.requests[1]
    assert second.response_schema["properties"]["next"]["enum"] == ["writer", "critic"]
    assert all(
        m.content.startswith("<untrusted")
        for m in second.messages
        if m.name in ("writer", "critic")
    )
