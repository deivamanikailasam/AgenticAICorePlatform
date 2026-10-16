"""Step 36 "Done when", through the run API with scripted agents: a change that lowers task
success beyond the threshold is blocked; a change that raises cost beyond the limit waits for
explicit approval. Plus which apps a change touches, and which reviews count as approval."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from agent_api.eval_apps import SpecHarness
from agent_api.eval_runner import Provider, evaluate
from agent_compiler.team_eval import StandIn, respond
from agent_contracts.models import ChatRequest, ChatResponse
from agent_evals.gates import GatesConfig, RunSummary
from agent_evals.gates import evaluate as judge
from agent_evals.generators import build
from agent_evals.tasks import dump_tasks

from scripts.eval_gate import affected, approvals_from_reviews

ROOT = Path(__file__).resolve().parents[3]
GATES = GatesConfig.load(ROOT / "evals" / "gates.yaml")


@pytest.fixture(autouse=True)
def _presidio(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from tests.stack.fakes.presidio_fake import presidio

    with presidio() as url:
        monkeypatch.setenv("P0_PRESIDIO_ANALYZER_URL", url)
        monkeypatch.setenv("P0_PRESIDIO_ANONYMIZER_URL", url)
        yield


def misroutes(agent: str, prompt: str) -> dict[str, Any]:
    if agent.endswith("-triage") and re.search("refund|invoice|charge", prompt, re.I):
        return {"handoff_to": "tech", "note": "sending to tech"}
    return respond(agent, prompt)


class Pricier(StandIn):
    """The same answers at twice the price per call (e.g. a bigger model)."""

    async def chat(self, request: ChatRequest) -> ChatResponse:
        r = await super().chat(request)
        return r.model_copy(
            update={"usage": r.usage.model_copy(update={"cost_aud": r.usage.cost_aud * 2})}
        )


async def summary(tasks_file: Path, make: Callable[[str], Any]) -> RunSummary:
    prov = Provider("scripted", lambda spec: make(spec.name))
    report = await evaluate(SpecHarness("support-handoff"), tasks_file, prov)
    return RunSummary.from_report(report.to_json())


@pytest.fixture
def tasks_file(tmp_path: Path) -> Path:
    tasks = build("support-handoff")
    sample = [t for t in tasks if "billing" in t.tags][:4] + [
        t for t in tasks if "general" in t.tags
    ][:2]
    path = tmp_path / "tasks.jsonl"
    dump_tasks(sample, path)
    return path


async def test_a_change_that_lowers_task_success_is_blocked(tasks_file: Path) -> None:
    baseline = await summary(tasks_file, lambda name: StandIn(name, respond))
    assert baseline.rate == 1.0
    changed = await summary(tasks_file, lambda name: StandIn(name, misroutes))
    result = judge("support-handoff", "low", GATES.for_app("low"), changed, baseline)
    assert result.verdict == "blocked"
    failed = {c.name: c.detail for c in result.checks if c.status == "fail"}
    assert set(failed) == {"success_drop"}  # 4 of 6 now fail: within 5 new failures, not 5 pp
    assert "success 33.3% vs baseline 100.0%" in failed["success_drop"]


async def test_a_cost_increase_needs_explicit_approval(tasks_file: Path) -> None:
    baseline = await summary(tasks_file, lambda name: StandIn(name, respond))
    assert baseline.cost_aud > 0
    assert baseline.tokens > 0
    dearer = await summary(tasks_file, lambda name: Pricier(name, respond))
    gate = GATES.for_app("low")
    waiting = judge("support-handoff", "low", gate, dearer, baseline)
    assert waiting.verdict == "needs_approval"
    growth = next(c for c in waiting.checks if c.name == "cost_growth")
    assert "(+100.0%; approval above +25%)" in growth.detail
    assert (
        judge("support-handoff", "low", gate, dearer, baseline, approvals={"cost"}).verdict
        == "pass"
    )


def test_which_apps_a_change_touches() -> None:
    apps = ["invoice-triage", "support-handoff"]
    assert affected(["agents/support-handoff/workers/billing.yaml"], apps) == ["support-handoff"]
    assert affected(["apps/invoice-triage/src/invoice_triage/functions.py"], apps) == [
        "invoice-triage"
    ]
    assert affected(["evals/baselines/invoice-triage.mechanics.json"], apps) == ["invoice-triage"]
    assert affected(["platform.yaml"], apps) == apps  # models
    assert affected(["libs/agent-compiler/src/agent_compiler/patterns.py"], apps) == apps
    assert affected(["docs/guide/x.md", "README.md"], apps) == []


def test_only_explicit_approval_from_an_approver_on_the_head_commit_counts() -> None:
    def review(
        user: str, body: str, commit: str = "head", state: str = "APPROVED"
    ) -> dict[str, Any]:
        return {"user": {"login": user}, "body": body, "commit_id": commit, "state": state}

    approvers = ["@carol", "@org/finance"]
    reviews = [
        review("dave", "LGTM"),  # author
        review("mallory", "/approve-cost"),  # not an approver
        review("carol", "/approve-cost", commit="old"),  # an earlier commit
        review("carol", "/approve-cost", state="COMMENTED"),
    ]
    assert approvals_from_reviews(reviews, "head", "dave", approvers) == set()
    assert approvals_from_reviews(
        [*reviews, review("carol", "Fine. /approve-cost /eval-signoff")], "head", "dave", approvers
    ) == {"cost", "signoff"}
    assert approvals_from_reviews([review("carol", "LGTM")], "head", "dave", approvers) == set()
    assert (
        approvals_from_reviews([review("dave", "/approve-cost")], "head", "dave", ["@dave"])
        == set()
    )
