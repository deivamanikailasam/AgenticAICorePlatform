"""Step 34: deterministic checkers, the rubric judge and its calibration, results and comparison."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent_contracts.dummies.models import ScriptedModelClient
from agent_contracts.models import Message, Role
from agent_evals.checks import Observation, ToolCallRecord, score, success
from agent_evals.judge import (
    Judge,
    JudgeError,
    Rubric,
    Verdict,
    calibrate,
    cohen_kappa,
    human_labels,
)
from agent_evals.results import Report, TaskResult, compare, publish, save
from agent_evals.tasks import Task, dump_tasks, load_tasks

PAY_SCHEMA = {
    "type": "object",
    "properties": {"invoice_id": {"type": "string"}, "amount_aud": {"type": "number"}},
    "required": ["invoice_id", "amount_aud"],
}


def task(**expected: object) -> Task:
    return Task.model_validate(
        {"id": "t1", "app": "invoice-triage", "input": {}, "expected": expected}
    )


def paid(amount: float = 528.0, approved: bool = True) -> Observation:
    return Observation(
        status="succeeded",
        output={"paid": True, "amount_aud": amount, "note": "Paid INV-1001 after approval"},
        tool_calls=[
            ToolCallRecord("get_invoice", {"invoice_id": "INV-1001"}, True),
            ToolCallRecord("post_payment", {"invoice_id": "INV-1001", "amount_aud": amount}, True),
        ],
        approvals_requested=["post_payment"] if approved else [],
        steps=4,
        tokens=900,
        cost_aud=0.01,
        latency_ms=1200,
        schemas={"post_payment": PAY_SCHEMA},
    )


# ---------------------------------------------------------------- deterministic checks


def test_a_correct_run_passes_every_named_check() -> None:
    t = task(
        status="succeeded",
        answer={"contains": ["INV-1001"], "path": "amount_aud", "number": 528.0},
        tools={
            "expected": ["get_invoice", "post_payment"],
            "arguments": {"post_payment": {"amount_aud": 528.0}},
        },
        policy={"approval_required": ["post_payment"]},
        limits={"max_steps": 10, "max_cost_aud": 0.05},
    )
    scores = score(t, paid())
    assert {s.metric for s in scores} == {
        "task_success",
        "answer_correct",
        "trajectory_order",
        "unnecessary_steps",
        "tool_call_accuracy",
        "policy_compliance",
        "within_limits",
    }
    assert success(scores), [s for s in scores if not s.passed]


def test_each_failure_is_named() -> None:
    t = task(
        answer={"path": "amount_aud", "number": 528.0},
        tools={"expected": ["get_invoice", "post_payment"], "forbidden": ["delete_invoice"]},
        policy={"approval_required": ["post_payment"]},
        limits={"max_steps": 3},
    )
    obs = paid(amount=5280.0, approved=False)
    obs.tool_calls.append(ToolCallRecord("delete_invoice", {}, True))
    failed = {s.metric: s.detail for s in score(t, obs) if not s.passed}
    assert "5280.0 is not 528.0" in failed["answer_correct"]
    assert failed["unnecessary_steps"] == "1 unnecessary call(s), 0 allowed"
    assert failed["tools_required_forbidden"] == "called forbidden delete_invoice"
    assert failed["policy_compliance"] == "post_payment ran without an approval"
    assert failed["within_limits"] == "steps 4 > 3"


def test_out_of_order_calls_and_invalid_arguments_are_caught() -> None:
    obs = paid()
    obs.tool_calls.reverse()
    obs.tool_calls[0] = ToolCallRecord(
        "post_payment", {"invoice_id": "INV-1001"}, True
    )  # no amount
    scores = {
        s.metric: s for s in score(task(tools={"expected": ["get_invoice", "post_payment"]}), obs)
    }
    assert scores["trajectory_order"].value == 0.5
    assert not scores["tool_call_accuracy"].passed
    assert "'amount_aud' is a required property" in scores["tool_call_accuracy"].detail


def test_a_denied_tool_must_be_denied_and_not_run() -> None:
    obs = paid()
    obs.denied.append("post_payment")
    s = score(task(policy={"denied": ["post_payment"]}), obs)[-1]
    assert s.detail == "post_payment ran although policy should deny it"


def test_unnamed_checks_are_not_scored() -> None:
    assert [s.metric for s in score(task(status="succeeded"), Observation("succeeded", None))] == [
        "task_success"
    ]


def test_task_sets_round_trip_and_refuse_duplicate_ids(tmp_path: Path) -> None:
    t = task(status="succeeded")
    dump_tasks([t, t.model_copy(update={"id": "t2"})], tmp_path / "tasks.jsonl")
    assert [x.id for x in load_tasks(tmp_path / "tasks.jsonl")] == ["t1", "t2"]
    dump_tasks([t, t], tmp_path / "dupe.jsonl")
    with pytest.raises(ValueError, match="duplicate task ids"):
        load_tasks(tmp_path / "dupe.jsonl")


# ---------------------------------------------------------------- judge and calibration


RUBRIC = """---
name: grounded
labels: [pass, fail]
---
# Grounded answer
Pass when every claim is supported by the cited documents; fail otherwise.
"""


def rubric(tmp_path: Path) -> Rubric:
    (tmp_path / "grounded.md").write_text(RUBRIC)
    return Rubric.load(tmp_path / "grounded.md")


async def test_the_judge_answers_with_a_rubric_label(tmp_path: Path) -> None:
    model = ScriptedModelClient(
        [Message(role=Role.ASSISTANT, content='{"label": "pass", "reason": "cited"}')]
    )
    verdict = await Judge(model, rubric(tmp_path)).grade("i1", {"q": "x"}, "answer")
    assert (verdict.label, verdict.reason) == ("pass", "cited")
    sent = model.requests[0]
    assert "Pass when every claim" in sent.messages[0].content  # the rubric is the instruction
    assert sent.temperature == 0
    assert sent.response_schema["properties"]["label"]["enum"] == ["pass", "fail"]


async def test_a_judge_answer_outside_the_rubric_is_an_error(tmp_path: Path) -> None:
    for content in ("looks fine to me", '{"label": "great", "reason": ""}'):
        model = ScriptedModelClient([Message(role=Role.ASSISTANT, content=content)])
        with pytest.raises(JudgeError):
            await Judge(model, rubric(tmp_path)).grade("i1", {}, "x")


def test_a_rubric_needs_labels_and_criteria(tmp_path: Path) -> None:
    (tmp_path / "bad.md").write_text("---\nname: x\nlabels: [only]\n---\n")
    with pytest.raises(ValueError, match="at least two labels"):
        Rubric.load(tmp_path / "bad.md")


def test_kappa_matches_a_hand_computed_example() -> None:
    # judge: p p f f; human: p f f f -> observed 0.75, chance 0.5, kappa 0.5
    observed, kappa, n = cohen_kappa([("p", "p"), ("p", "f"), ("f", "f"), ("f", "f")])
    assert (observed, kappa, n) == (0.75, 0.5, 4)


def test_without_human_labels_agreement_is_not_measured(tmp_path: Path) -> None:
    result = calibrate(rubric(tmp_path), [Verdict("i1", "pass", "")], tmp_path / "none.jsonl")
    assert not result.measured
    assert result.agreement is None
    assert "not measured" in result.summary()


def test_only_named_human_labels_calibrate(tmp_path: Path) -> None:
    labels = tmp_path / "grounded.jsonl"
    labels.write_text(
        json.dumps({"item": "i1", "label": "pass", "labeller_kind": "model", "labeller": "x"})
        + "\n"
    )
    with pytest.raises(ValueError, match="not a human label"):
        human_labels(labels)
    rows = [
        {"item": f"i{i}", "label": h, "labeller": "Reviewer A", "labeller_kind": "human"}
        for i, h in enumerate(["pass", "fail", "fail", "fail"])
    ]
    labels.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    verdicts = [Verdict(f"i{i}", j, "") for i, j in enumerate(["pass", "pass", "fail", "fail"])]
    result = calibrate(rubric(tmp_path), verdicts, labels)
    assert (result.measured, result.items, result.agreement, result.kappa) == (True, 4, 0.75, 0.5)
    assert result.labellers == ("Reviewer A",)


# ---------------------------------------------------------------- results


def report(kind: str, outcomes: dict[str, bool]) -> Report:
    return Report(
        app="invoice-triage",
        versions={"spec": "0.3.0", "spec_hash": "abc"},
        dataset={
            "path": "evals/apps/invoice-triage/tasks.jsonl",
            "sha256": "d1",
            "tasks": len(outcomes),
        },
        provider={"kind": kind, "models": {"default": "local-qwen-fast"}},
        commit="c0ffee",
        date="2026-10-16T02:00:00+00:00",
        results=[
            TaskResult(t, ok, [], 3, 100, 0.001, 100 * (i + 1), "succeeded")
            for i, (t, ok) in enumerate(outcomes.items())
        ],
    )


def test_mock_runs_are_never_published_as_a_baseline(tmp_path: Path) -> None:
    for kind in ("scripted", "stand-in"):
        with pytest.raises(ValueError, match="only real-model runs"):
            publish(report(kind, {"a": True}), tmp_path)
    card = json.loads(publish(report("gateway", {"a": True, "b": False}), tmp_path).read_text())
    assert (card["passed"], card["total"], card["model"]) == (1, 2, "local-qwen-fast")


def test_results_are_saved_with_versions_and_compared(tmp_path: Path) -> None:
    a, b = (
        report("gateway", {"a": True, "b": False, "c": True}),
        report("gateway", {"a": False, "b": True, "c": True}),
    )
    b.versions["spec"] = "0.4.0"
    doc_a = json.loads(save(a, tmp_path).read_text())
    assert doc_a["aggregates"]["success_rate"] == pytest.approx(2 / 3)
    assert doc_a["aggregates"]["latency_ms_p50"] == 200
    assert doc_a["baseline_eligible"] is True
    diff = compare(doc_a, b.to_json())
    assert diff["regressions"] == ["a"]
    assert diff["fixed"] == ["b"]
    assert diff["versions"] == {"spec": {"from": "0.3.0", "to": "0.4.0"}}


def test_approval_requests_show_the_right_arguments() -> None:
    obs = Observation("interrupted", None, approvals_requested=["post_payment"])
    obs.approval_arguments["post_payment"] = {"invoice_id": "INV-9", "amount_aud": 99.0}
    t = task(
        status=["interrupted", "succeeded"],
        policy={
            "approval_required": ["post_payment"],
            "approval_arguments": {"post_payment": {"amount_aud": 528.0}},
        },
        limits={"max_model_calls": 0},
    )
    failed = {s.metric: s.detail for s in score(t, obs) if not s.passed}
    assert failed == {
        "policy_compliance": "approval asks post_payment.amount_aud = 99.0, expected 528.0"
    }
    held = score(task(policy={"not_requested": ["post_payment"]}), obs)[0]
    assert held.detail == "post_payment reached an approval request although it must not"


def test_team_trajectories_and_word_limits() -> None:
    obs = Observation(
        "succeeded",
        "Refund approved for invoice 12 today",
        delegations=["triage", "tech", "billing"],
    )
    t = task(
        answer={"max_words": 4},
        team={
            "first_worker": "triage",
            "workers_required": ["billing"],
            "workers_forbidden": ["tech"],
            "counts": {"billing": 1},
            "max_delegations": 2,
        },
    )
    failed = {s.metric: s.detail for s in score(t, obs) if not s.passed}
    assert failed["answer_correct"] == "6 words, at most 4"
    assert failed["team_trajectory"] == "delegated to tech; 3 delegations, at most 2"
