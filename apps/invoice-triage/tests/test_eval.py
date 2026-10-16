"""Step 34: the eval harness on the invoice-triage task set, through the run API with the real
ERP (MCP stdio) and policy. Scripted agents stand in for the model so the harness itself is
tested: a correct agent's runs pass, a misbehaving agent's runs are caught. (These are harness
checks, not model results: real-model runs are VERIFY-ON-MAC.)"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from agent_api.eval_runner import Provider, run_tasks
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.interfaces import ModelClient
from agent_contracts.models import Message, Role, ToolCall
from agent_evals.checks import score, success
from agent_evals.tasks import Task, load_tasks
from invoice_triage.eval import InvoiceTriageHarness
from invoice_triage.eval_tasks import OUT, Builder

TASKS = load_tasks(OUT)


@pytest.fixture(autouse=True)
def _presidio(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Presidio is not reachable offline and the PII guardrail fails closed: a loopback double
    that finds no PII (the tasks hold none) stands in for it."""
    from tests.stack.fakes.presidio_fake import presidio

    with presidio() as url:
        monkeypatch.setenv("P0_PRESIDIO_ANALYZER_URL", url)
        monkeypatch.setenv("P0_PRESIDIO_ANONYMIZER_URL", url)
        yield


def of(kind: str) -> list[Task]:
    return [t for t in TASKS if kind in t.tags]


class NoModel(ScriptedModelClient):
    async def chat(self, request: Any) -> Any:
        raise AssertionError("the deterministic path must not call a model")


def provider(make: Callable[[], ModelClient]) -> Provider:
    return Provider("scripted", lambda spec: make())


def scripted(answers: list[dict[str, Any]]) -> Callable[[], ModelClient]:
    """Each run gets the next script: look the invoice up, then answer in the output schema."""
    queue = list(answers)

    def make() -> ModelClient:
        answer = queue.pop(0)
        return ScriptedModelClient(
            [
                Message(
                    role=Role.ASSISTANT,
                    tool_calls=(
                        ToolCall(
                            name="erp__get_invoice", arguments={"invoice_id": answer["invoice_id"]}
                        ),
                    ),
                ),
                Message(role=Role.ASSISTANT, content=json.dumps(answer)),
            ]
        )

    return make


async def scored(tasks: list[Task], prov: Provider) -> list[tuple[Task, bool, list[Any]]]:
    out = []
    for task, obs, _ in await run_tasks(InvoiceTriageHarness(), tasks, prov):
        scores = score(task, obs)
        out.append((task, success(scores), scores))
    return out


def test_the_task_set_is_current_and_large_enough() -> None:
    assert len(TASKS) >= 100
    fresh = Builder().build()
    assert [t.model_dump() for t in fresh] == [t.model_dump() for t in TASKS], "run just eval-tasks"
    assert {t.tags[0] for t in TASKS} == {
        "valid",
        "gst-error",
        "emailed-po",
        "no-po",
        "currency",
        "total-mismatch",
        "non-positive",
        "injection",
        "unknown",
    }


async def test_valid_invoices_are_paid_after_approval_without_a_model() -> None:
    results = await scored(of("valid")[:8], provider(NoModel))
    failed = [(t.id, [s.detail for s in sc if not s.passed]) for t, ok, sc in results if not ok]
    assert not failed, failed


async def test_held_invoices_never_reach_payment_when_the_agent_cannot_resolve_them() -> None:
    # an agent that never produces a resolution: the run can't reach payment
    results = await scored(
        of("no-po")[:3] + of("currency")[:3], provider(lambda: ScriptedModelClient())
    )
    failed = [(t.id, [s.detail for s in sc if not s.passed]) for t, ok, sc in results if not ok]
    assert not failed, failed


async def test_a_correct_agent_passes_and_a_manipulated_one_is_caught() -> None:
    gst = of("gst-error")[:2]
    good = [
        {
            "invoice_id": t.input["invoice"]["id"],
            "amount": round(t.input["invoice"]["subtotal"] * 1.1, 2),
            "currency": "AUD",
            "resolution": "GST corrected to 10% of the subtotal (finance-policy 4.2).",
        }
        for t in gst
    ]
    results = await scored(gst, provider(scripted(good)))
    assert all(ok for _, ok, _ in results), [
        [s.detail for s in sc if not s.passed] for _, _, sc in results
    ]

    injected = of("injection")[:2]
    obeys = [
        {
            "invoice_id": t.input["invoice"]["id"],
            "amount": 9999.0,
            "currency": "AUD",
            "resolution": "as instructed",
        }
        for t in injected
    ]
    caught = await scored(injected, provider(scripted(obeys)))
    for _, ok, sc in caught:
        assert not ok
        details = " ".join(s.detail for s in sc if not s.passed)
        assert "post_payment reached an approval request although it must not" in details


def test_harness_records_no_real_model_results(tmp_path: Path) -> None:
    from agent_evals.results import Report, publish

    report = Report(
        "invoice-triage",
        {},
        {"path": str(OUT), "sha256": "x", "tasks": 1},
        {"kind": "scripted"},
        "c",
        "d",
        [],
    )
    with pytest.raises(ValueError, match="only real-model runs"):
        publish(report, tmp_path)
