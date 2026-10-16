"""Step 34: the eval harness on the spec-only reference apps, through the run API.

Rule-based stand-in agents (like Step 23's team eval) play the models, so these test the harness
and the platform's team mechanics, not model quality: a well-behaved team passes its tasks and a
misbehaving one is caught. hello-workflow has no model at all, so its result here is the app's
real result.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from agent_api.eval_apps import SpecHarness
from agent_api.eval_runner import Provider, run_tasks
from agent_compiler.team_eval import StandIn, respond
from agent_evals.checks import score, success
from agent_evals.generators import GENERATORS, build, path_for
from agent_evals.tasks import Task, load_tasks


@pytest.fixture(autouse=True)
def _presidio(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from tests.stack.fakes.presidio_fake import presidio

    with presidio() as url:
        monkeypatch.setenv("P0_PRESIDIO_ANALYZER_URL", url)
        monkeypatch.setenv("P0_PRESIDIO_ANONYMIZER_URL", url)
        yield


def well_behaved(agent: str, prompt: str) -> dict[str, Any]:
    """Step 23's stand-ins, with a reducer that keeps what the mappers said."""
    if agent.endswith("-reducer"):
        return {
            "message": "Digest: " + " ".join(re.findall(r"sales in ([A-Z][a-z]+(?: \d+)?)", prompt))
        }
    return respond(agent, prompt)


def misroutes(agent: str, prompt: str) -> dict[str, Any]:
    """A triage agent that sends everything to tech."""
    if agent.endswith("-triage"):
        return {"handoff_to": "tech", "note": "sending to tech"}
    return respond(agent, prompt)


def provider(policy: Callable[[str, str], dict[str, Any]]) -> Provider:
    return Provider("scripted", lambda spec: StandIn(spec.name, policy))


async def scored(app: str, tasks: list[Task], prov: Provider) -> list[tuple[Task, bool, list[Any]]]:
    out = []
    for task, obs, _ in await run_tasks(SpecHarness(app), tasks, prov):
        scores = score(task, obs)
        out.append((task, success(scores), scores))
    return out


def failures(results: list[tuple[Task, bool, list[Any]]]) -> list[Any]:
    return [(t.id, [s.detail for s in sc if not s.passed]) for t, ok, sc in results if not ok]


def test_every_reference_app_has_a_current_set_of_at_least_100_tasks() -> None:
    for app in GENERATORS:
        tasks = load_tasks(path_for(app))
        assert len(tasks) >= 100, app
        assert [t.model_dump() for t in tasks] == [t.model_dump() for t in build(app)], (
            f"{app}: just eval-tasks"
        )


async def test_hello_workflow_passes_every_task_without_a_model() -> None:
    tasks = load_tasks(path_for("hello-workflow"))
    results = await scored(
        "hello-workflow", tasks, Provider("scripted", lambda spec: StandIn(spec.name, respond))
    )
    assert not failures(results)
    assert len(results) == 100


async def test_support_handoff_routes_and_a_misrouting_team_is_caught() -> None:
    tasks = load_tasks(path_for("support-handoff"))
    sample = [t for t in tasks if "billing" in t.tags][:2] + [t for t in tasks if "tech" in t.tags][
        :2
    ]
    sample += [t for t in tasks if "general" in t.tags][:2]
    good = await scored("support-handoff", sample, provider(well_behaved))
    billing_ok = [t for t, ok, _ in good if "billing" in t.tags and ok]
    assert len(billing_ok) == 2, failures(good)
    bad = await scored(
        "support-handoff", [t for t in sample if "billing" in t.tags], provider(misroutes)
    )
    for _, ok, sc in bad:
        assert not ok
        assert "delegated to tech" in " ".join(s.detail for s in sc)


async def test_batch_summary_counts_mappers_and_refuses_oversized_batches() -> None:
    tasks = load_tasks(path_for("batch-summary"))
    sample = [t for t in tasks if "digest" in t.tags][:3] + [
        t for t in tasks if "over-limit" in t.tags
    ][:1]
    results = await scored("batch-summary", sample, provider(well_behaved))
    assert not failures(results)


async def test_debate_and_review_teams_take_part_and_decide() -> None:
    debate = load_tasks(path_for("policy-debate"))[:2]
    assert not failures(await scored("policy-debate", debate, provider(well_behaved)))
    kettles = [t for t in load_tasks(path_for("review-chat")) if "kettle" in t.input["task"]][:2]
    assert not failures(await scored("review-chat", kettles, provider(well_behaved)))


async def test_research_tasks_carry_their_sources_for_the_search_tool() -> None:
    harness = SpecHarness("research-report")
    tasks = load_tasks(path_for("research-report"))[:3]
    await harness.prepare(tasks)
    refs = {t.ref for t in await (await harness.registry(harness.spec())).list()}
    assert "mcp://rag-core/search" in refs
    request = httpx.Request(
        "POST", "http://rag-core.eval", content=json.dumps({"query": tasks[0].input["task"]})
    )
    found = harness._search(request).json()["results"]  # the rag-core API the tool talks to
    assert {r["id"] for r in found} >= {d["id"] for d in tasks[0].input["_corpus"]}
