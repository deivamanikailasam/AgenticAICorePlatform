"""Step 36: the eval gates: thresholds per risk tier, what blocks, what needs approval, and what
applies only to real-model runs."""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_evals.gates import GatesConfig, RunSummary, evaluate, markdown
from agent_evals.redteam import Report, Result

ROOT = Path(__file__).resolve().parents[3]
GATES = GatesConfig.load(ROOT / "evals" / "gates.yaml")


def run(
    passed: int, total: int = 100, tokens: int = 1000, aud: float = 0.10, **kw: object
) -> RunSummary:
    tasks = {f"t{i:03d}": i < passed for i in range(total)}
    return RunSummary(
        kind=kw.pop("kind", "mechanics"),  # type: ignore[arg-type]
        dataset_sha="d1",
        tasks=tasks,
        tokens=tokens,
        cost_aud=aud,
        **kw,  # type: ignore[arg-type]
    )


def status(result: object, name: str) -> str:
    return next(c.status for c in result.checks if c.name == name)  # type: ignore[attr-defined]


def clean_redteam(*findings: str) -> Report:
    rows = [Result(f"r{i}", "ASI01", "x", sev, "finding") for i, sev in enumerate(findings)]  # type: ignore[arg-type]
    rows.append(Result("ok", "ASI02", "y", "high", "held"))
    return Report("c", "d", "opa", rows)


def test_the_committed_gates_cover_every_tier_and_get_stricter() -> None:
    low, medium, high = (GATES.for_app(t) for t in ("low", "medium", "high"))
    assert low.task_success.min_rate < medium.task_success.min_rate < high.task_success.min_rate
    assert high.task_success.max_new_failures == 0
    assert not low.redteam.required
    assert medium.redteam.required
    assert high.redteam.required
    assert high.signoff
    assert not medium.signoff
    with pytest.raises(ValueError, match="no gate set named"):
        GATES.for_app("low", "nonsense")


def test_an_unchanged_run_passes() -> None:
    result = evaluate("a", "medium", GATES.for_app("medium"), run(90), run(90), clean_redteam())
    assert result.verdict == "pass", result.checks
    assert status(result, "min_success") == "n/a"  # quality floors don't apply to a stand-in run
    assert status(result, "latency") == "n/a"


def test_a_change_that_lowers_task_success_beyond_the_threshold_is_blocked() -> None:
    gate = GATES.for_app("medium")  # at most 2 new failures, at most -2 pp
    two = evaluate("a", "medium", gate, run(88), run(90), clean_redteam())
    assert two.verdict == "pass"
    three = evaluate("a", "medium", gate, run(87), run(90), clean_redteam())
    assert three.verdict == "blocked"
    assert status(three, "new_failures") == "fail"
    assert status(three, "success_drop") == "fail"
    detail = next(c.detail for c in three.checks if c.name == "new_failures")
    assert detail.startswith("3 task(s) that passed in the baseline now fail (at most 2): t087")


def test_a_cost_increase_above_the_limit_needs_explicit_approval() -> None:
    gate = GATES.for_app("low")  # approval above +25% AUD or tokens per task
    small = evaluate("a", "low", gate, run(90, aud=0.12, tokens=1200), run(90), None)
    assert small.verdict == "pass"
    big = evaluate("a", "low", gate, run(90, aud=0.13, tokens=1300), run(90), None)
    assert big.verdict == "needs_approval"
    assert status(big, "cost_growth") == "needs_approval"
    assert status(big, "token_growth") == "needs_approval"
    approved = evaluate("a", "low", gate, run(90, aud=0.13, tokens=1300), run(90), None, {"cost"})
    assert approved.verdict == "pass"
    assert status(approved, "cost_growth") == "approved"
    first_spend = evaluate("a", "low", gate, run(90, aud=0.01), run(90, aud=0.0), None)
    assert "new spend (baseline 0)" in next(
        c.detail for c in first_spend.checks if c.name == "cost_growth"
    )


def test_red_team_findings_block_by_severity_and_are_required_above_low() -> None:
    medium = GATES.for_app("medium")
    assert evaluate("a", "medium", medium, run(90), run(90), None).verdict == "blocked"
    assert evaluate("a", "medium", medium, run(90), run(90), clean_redteam("low")).verdict == "pass"
    blocked = evaluate("a", "medium", medium, run(90), run(90), clean_redteam("high"))
    assert next(c.detail for c in blocked.checks if c.name == "redteam") == "1 high (at most 0)"
    low = GATES.for_app("low")
    assert status(evaluate("a", "low", low, run(90), run(90), None), "redteam") == "n/a"


def test_quality_runs_meet_absolute_floors_and_ceilings() -> None:
    gate = GATES.for_app("medium")
    base = run(95, kind="quality", aud=5.0, latency_p95_ms=10_000)
    good = run(95, kind="quality", aud=5.0, latency_p95_ms=11_000)  # AUD 0.05 per task
    result = evaluate("a", "medium", gate, good, base, clean_redteam())
    assert result.verdict == "pass", result.checks
    weak = evaluate(
        "a", "medium", gate, run(85, kind="quality", latency_p95_ms=10_000), None, clean_redteam()
    )
    assert status(weak, "min_success") == "fail"  # 85% < 90%
    slow = evaluate(
        "a", "medium", gate, run(95, kind="quality", latency_p95_ms=70_000), base, clean_redteam()
    )
    assert status(slow, "latency") == "fail"
    dear = evaluate(
        "a",
        "medium",
        gate,
        run(95, kind="quality", aud=20.0, latency_p95_ms=10_000),
        base,
        clean_redteam(),
        {"cost"},
    )
    assert status(dear, "cost_ceiling") == "approved"


def test_a_changed_task_set_needs_a_new_baseline_and_high_risk_needs_signoff() -> None:
    other = run(90)
    other.dataset_sha = "d2"
    result = evaluate("a", "low", GATES.for_app("low"), run(90), other, None)
    assert result.verdict == "blocked"
    assert "re-record it in this change" in result.checks[0].detail
    high = GATES.for_app("high")
    waiting = evaluate("a", "high", high, run(90), run(90), clean_redteam())
    assert waiting.verdict == "needs_approval"
    assert (
        evaluate("a", "high", high, run(90), run(90), clean_redteam(), {"signoff"}).verdict
        == "pass"
    )


def test_the_summary_reads_as_a_pull_request_comment() -> None:
    blocked = evaluate(
        "invoice-triage", "medium", GATES.for_app("medium"), run(80), run(90), clean_redteam()
    )
    costly = evaluate("review-chat", "low", GATES.for_app("low"), run(90, aud=0.5), run(90), None)
    text = markdown([blocked, costly], ["Stand-in run."])
    assert "### invoice-triage (medium risk, mechanics run): **blocked**" in text
    assert "| new_failures | ❌ fail |" in text
    assert "### review-chat (low risk, mechanics run): **needs_approval**" in text
    assert "`/approve-cost`" in text
    assert text.endswith("_Stand-in run._\n")
