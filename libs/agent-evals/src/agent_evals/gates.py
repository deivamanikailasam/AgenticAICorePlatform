"""CI eval gates (Step 36): decide whether a change may merge, from its eval run, the baseline,
the red-team report and any explicit approvals.

``evals/gates.yaml`` sets thresholds per risk tier (low, medium, high), with named sets a spec can
choose in ``evals.gates``. A run is summarised as tasks passed and failed, tokens, AUD and model
calls per task, and latency. Its ``kind`` decides which checks apply:

* ``mechanics``: a stand-in or scripted run. Checked against the mechanics baseline: no new task
  failures beyond the limit, no drop in success rate beyond the limit, no growth in tokens or AUD
  per task beyond the limit.
* ``quality``: a real-model run. Additionally checked against absolute floors and ceilings:
  success rate, AUD per task and latency.

Each check is ``pass``, ``fail`` (blocks the merge), ``needs_approval`` (a cost over its limit,
or a high-tier sign-off), ``approved`` or ``n/a`` (with the reason it doesn't apply). The
verdict is ``blocked`` if anything failed, ``needs_approval`` if an approval is missing, else
``pass``.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from agent_evals.redteam import Report as RedTeamReport

Status = Literal["pass", "fail", "needs_approval", "approved", "n/a"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskSuccessGate(_Model):
    min_rate: float = Field(ge=0, le=1)
    max_drop_pp: float = Field(ge=0)
    max_new_failures: int = Field(ge=0)


class RedTeamGate(_Model):
    required: bool
    max_findings: dict[str, int] = Field(default_factory=dict)


class CostGate(_Model):
    max_increase_pct: float = Field(ge=0)
    max_token_increase_pct: float = Field(ge=0)
    max_aud_per_task: float = Field(ge=0)


class LatencyGate(_Model):
    max_increase_pct: float = Field(ge=0)
    max_p95_ms: int = Field(ge=0)


class TierGate(_Model):
    task_success: TaskSuccessGate
    redteam: RedTeamGate
    cost: CostGate
    latency: LatencyGate
    signoff: bool = False


def _merge(base: dict[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, Mapping) and isinstance(out.get(k), dict) else v
    return out


@dataclass(frozen=True)
class GatesConfig:
    tiers: dict[str, dict[str, Any]]
    named: dict[str, dict[str, Any]]

    @staticmethod
    def load(path: str | Path) -> GatesConfig:
        doc = yaml.safe_load(Path(path).read_text()) or {}
        cfg = GatesConfig(dict(doc.get("tiers") or {}), dict(doc.get("named") or {}))
        for tier in ("low", "medium", "high"):
            if tier not in cfg.tiers:
                raise ValueError(f"{path}: no gates for risk tier {tier}")
            cfg.for_app(tier)  # validates
        return cfg

    def for_app(self, tier: str, name: str = "default") -> TierGate:
        if name not in self.named:
            raise ValueError(f"no gate set named {name!r} in evals/gates.yaml")
        return TierGate.model_validate(_merge(self.tiers[tier], self.named[name]))


@dataclass
class RunSummary:
    """One eval run (or a baseline) as the gates see it."""

    kind: Literal["mechanics", "quality"]
    dataset_sha: str
    tasks: dict[str, bool]
    tokens: int = 0
    cost_aud: float = 0.0
    model_calls: int = 0
    latency_p95_ms: int | None = None

    @property
    def rate(self) -> float:
        return sum(self.tasks.values()) / len(self.tasks) if self.tasks else 0.0

    def per_task(self, value: float) -> float:
        return value / len(self.tasks) if self.tasks else 0.0

    @staticmethod
    def from_report(doc: Mapping[str, Any]) -> RunSummary:
        """From a saved eval report (``agent_evals.results.save``)."""
        results = doc.get("results") or []
        return RunSummary(
            kind="quality" if doc.get("baseline_eligible") else "mechanics",
            dataset_sha=str((doc.get("dataset") or {}).get("sha256", "")),
            tasks={str(r["task"]): bool(r["success"]) for r in results},
            tokens=sum(int(r.get("tokens", 0)) for r in results),
            cost_aud=sum(float(r.get("cost_aud", 0.0)) for r in results),
            model_calls=sum(int(r.get("model_calls", 0)) for r in results),
            latency_p95_ms=(doc.get("aggregates") or {}).get("latency_ms_p95"),
        )

    @staticmethod
    def from_baseline(doc: Mapping[str, Any]) -> RunSummary:
        """From a committed baseline (``evals/baselines/<app>.<kind>.json``)."""
        tasks = doc.get("tasks") or {}
        usage = doc.get("usage") or {}
        return RunSummary(
            kind="quality" if doc.get("kind") == "quality" else "mechanics",
            dataset_sha=str((doc.get("dataset") or {}).get("sha256", "")),
            tasks={str(t): bool(v.get("success")) for t, v in tasks.items()},
            tokens=int(usage.get("tokens", 0)),
            cost_aud=float(usage.get("cost_aud", 0.0)),
            model_calls=int(usage.get("model_calls", 0)),
            latency_p95_ms=usage.get("latency_ms_p95"),
        )


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


@dataclass
class GateResult:
    app: str
    tier: str
    kind: str
    checks: list[Check] = field(default_factory=list)

    @property
    def verdict(self) -> Literal["pass", "needs_approval", "blocked"]:
        statuses = {c.status for c in self.checks}
        if "fail" in statuses:
            return "blocked"
        if "needs_approval" in statuses:
            return "needs_approval"
        return "pass"

    def to_json(self) -> dict[str, Any]:
        return {
            "app": self.app,
            "tier": self.tier,
            "kind": self.kind,
            "verdict": self.verdict,
            "checks": [c.__dict__ for c in self.checks],
        }


def _growth(before: float, after: float) -> float:
    if before > 0:
        return (after - before) / before * 100
    return math.inf if after > 0 else 0.0


def _pct(value: float) -> str:
    return "new spend (baseline 0)" if math.isinf(value) else f"{value:+.1f}%"


def evaluate(
    app: str,
    tier: str,
    gate: TierGate,
    current: RunSummary,
    baseline: RunSummary | None,
    redteam: RedTeamReport | None = None,
    approvals: Collection[str] = (),
) -> GateResult:
    """``approvals``: ``cost`` and/or ``signoff``, already verified by the caller."""
    out = GateResult(app, tier, current.kind)
    add = out.checks.append
    quality = current.kind == "quality"
    not_quality = "n/a: stand-in run (quality, AUD and latency are measured on real-model runs)"

    # ------------------------------------------------------------------ task success
    if baseline is None:
        add(Check("baseline", "fail", f"no {current.kind} baseline for {app}"))
    elif baseline.dataset_sha != current.dataset_sha:
        add(
            Check(
                "baseline",
                "fail",
                "the task set changed since the baseline: re-record it in this change "
                "(just eval-baseline)",
            )
        )
        baseline = None
    else:
        add(Check("baseline", "pass", f"{baseline.kind} baseline on the same task set"))
    if baseline is not None:
        broke = sorted(t for t, ok in baseline.tasks.items() if ok and not current.tasks.get(t))
        limit = gate.task_success.max_new_failures
        add(
            Check(
                "new_failures",
                "pass" if len(broke) <= limit else "fail",
                f"{len(broke)} task(s) that passed in the baseline now fail (at most {limit})"
                + (f": {', '.join(broke[:8])}" if broke else ""),
            )
        )
        drop = round((baseline.rate - current.rate) * 100, 6)  # no float noise at the limit
        add(
            Check(
                "success_drop",
                "pass" if drop <= gate.task_success.max_drop_pp else "fail",
                f"success {current.rate:.1%} vs baseline {baseline.rate:.1%} "
                f"({-drop:+.1f} pp; at most -{gate.task_success.max_drop_pp:g} pp)",
            )
        )
    if quality:
        add(
            Check(
                "min_success",
                "pass" if current.rate >= gate.task_success.min_rate else "fail",
                f"success {current.rate:.1%} on {len(current.tasks)} tasks "
                f"(at least {gate.task_success.min_rate:.0%})",
            )
        )
    else:
        add(Check("min_success", "n/a", not_quality))

    # ------------------------------------------------------------------ red team
    if redteam is None:
        add(
            Check(
                "redteam",
                "fail" if gate.redteam.required else "n/a",
                "no red-team report"
                + (" (required for this tier)" if gate.redteam.required else ""),
            )
        )
    else:
        counts: dict[str, int] = {}
        for r in redteam.findings():
            counts[r.severity] = counts.get(r.severity, 0) + 1
        over = [
            f"{n} {sev} (at most {gate.redteam.max_findings[sev]})"
            for sev, n in counts.items()
            if sev in gate.redteam.max_findings and n > gate.redteam.max_findings[sev]
        ]
        add(
            Check(
                "redteam",
                "fail" if over else "pass",
                "; ".join(over)
                or f"{len(redteam.findings())} finding(s), none above this tier's limits",
            )
        )

    # ------------------------------------------------------------------ cost
    ok_cost: Status = "approved" if "cost" in approvals else "needs_approval"
    if baseline is not None:
        aud = _growth(baseline.per_task(baseline.cost_aud), current.per_task(current.cost_aud))
        add(
            Check(
                "cost_growth",
                "pass" if aud <= gate.cost.max_increase_pct else ok_cost,
                f"AUD per task {current.per_task(current.cost_aud):.6f} vs "
                f"{baseline.per_task(baseline.cost_aud):.6f} ({_pct(aud)}; "
                f"approval above +{gate.cost.max_increase_pct:g}%)",
            )
        )
        tok = _growth(baseline.per_task(baseline.tokens), current.per_task(current.tokens))
        add(
            Check(
                "token_growth",
                "pass" if tok <= gate.cost.max_token_increase_pct else ok_cost,
                f"tokens per task {current.per_task(current.tokens):.1f} vs "
                f"{baseline.per_task(baseline.tokens):.1f} ({_pct(tok)}; "
                f"approval above +{gate.cost.max_token_increase_pct:g}%)",
            )
        )
    if quality:
        per_task = current.per_task(current.cost_aud)
        add(
            Check(
                "cost_ceiling",
                "pass" if per_task <= gate.cost.max_aud_per_task else ok_cost,
                f"AUD {per_task:.4f} per task (ceiling {gate.cost.max_aud_per_task:g})",
            )
        )
    else:
        add(Check("cost_ceiling", "n/a", not_quality))

    # ------------------------------------------------------------------ latency
    if quality and current.latency_p95_ms is not None:
        p95 = current.latency_p95_ms
        problems = []
        if p95 > gate.latency.max_p95_ms:
            problems.append(f"p95 {p95} ms > {gate.latency.max_p95_ms} ms")
        if baseline is not None and baseline.latency_p95_ms:
            grew = _growth(baseline.latency_p95_ms, p95)
            if grew > gate.latency.max_increase_pct:
                problems.append(
                    f"p95 {_pct(grew)} vs baseline (at most +{gate.latency.max_increase_pct:g}%)"
                )
        add(
            Check("latency", "fail" if problems else "pass", "; ".join(problems) or f"p95 {p95} ms")
        )
    else:
        add(Check("latency", "n/a", not_quality))

    # ------------------------------------------------------------------ sign-off
    if gate.signoff:
        add(
            Check(
                "signoff",
                "approved" if "signoff" in approvals else "needs_approval",
                "high-risk tier: a named approver signs off this change (/eval-signoff)",
            )
        )
    return out


ICONS = {"pass": "✅", "fail": "❌", "needs_approval": "✋", "approved": "☑️", "n/a": "-"}


def markdown(results: Sequence[GateResult], notes: Sequence[str] = ()) -> str:
    lines = ["## Eval gates", ""]
    for r in results:
        lines.append(f"### {r.app} ({r.tier} risk, {r.kind} run): **{r.verdict}**")
        lines.append("")
        lines.append("| Check | Result | Detail |")
        lines.append("|---|---|---|")
        lines += [f"| {c.name} | {ICONS[c.status]} {c.status} | {c.detail} |" for c in r.checks]
        lines.append("")
    if any(r.verdict == "needs_approval" for r in results):
        lines.append(
            "✋ **Needs explicit approval:** an approver for the tier approves the head commit "
            "with a review containing `/approve-cost` (and `/eval-signoff` for high-risk apps), "
            "then re-runs this job."
        )
        lines.append("")
    lines += [f"_{n}_" for n in notes]
    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "Check",
    "GateResult",
    "GatesConfig",
    "RunSummary",
    "TierGate",
    "evaluate",
    "markdown",
]
