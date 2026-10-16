"""Eval results with the versions they ran on, and comparison between two runs (Step 34).

A report records: the app and its versions (spec version and hash, plan hash, models per role,
tool versions: the same identifiers as the audit trail), the task set (path, SHA-256, size), the
model provider, the git commit, the date, each task's scores, and the aggregates.

``provider.kind`` says what produced the outputs:

* ``scripted`` — the harness's own scripted model: checks the harness and the platform's
  deterministic parts, says nothing about model quality;
* ``stand-in`` — the stand-in gateway's canned mock responses: the same;
* ``gateway`` — real models behind the gateway (Project 0 on the Mac).

Only ``gateway`` runs are eligible to be a baseline or to be cited on agent cards
(``publish``); the others are labelled as mechanics checks wherever they appear.
"""

from __future__ import annotations

import json
import statistics
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_evals.checks import Score

REAL = "gateway"


@dataclass
class TaskResult:
    task: str
    success: bool
    scores: list[Score]
    steps: int
    tokens: int
    cost_aud: float
    latency_ms: int
    status: str
    judged: dict[str, str] = field(default_factory=dict)  # rubric -> label
    error: str = ""


@dataclass
class Report:
    app: str
    versions: dict[str, Any]
    dataset: dict[str, Any]
    provider: dict[str, Any]
    commit: str
    date: str
    results: list[TaskResult]
    calibration: list[dict[str, Any]] = field(default_factory=list)

    @property
    def baseline_eligible(self) -> bool:
        return self.provider.get("kind") == REAL

    def aggregates(self) -> dict[str, Any]:
        n = len(self.results)
        metrics: dict[str, list[Score]] = {}
        for r in self.results:
            for s in r.scores:
                metrics.setdefault(s.metric, []).append(s)
        latencies = sorted(r.latency_ms for r in self.results)
        return {
            "tasks": n,
            "succeeded": sum(r.success for r in self.results),
            "success_rate": sum(r.success for r in self.results) / n if n else 0.0,
            "metrics": {
                m: {
                    "scored": len(v),
                    "passed": sum(s.passed for s in v),
                    "mean": round(statistics.fmean(s.value for s in v), 4),
                }
                for m, v in sorted(metrics.items())
            },
            "steps_mean": round(statistics.fmean(r.steps for r in self.results), 2) if n else 0,
            "tokens_total": sum(r.tokens for r in self.results),
            "cost_aud_total": round(sum(r.cost_aud for r in self.results), 6),
            "latency_ms_p50": latencies[n // 2] if n else 0,
            "latency_ms_p95": latencies[min(n - 1, int(n * 0.95))] if n else 0,
        }

    def to_json(self) -> dict[str, Any]:
        doc = asdict(self)
        doc["aggregates"] = self.aggregates()
        doc["baseline_eligible"] = self.baseline_eligible
        return doc


def git_commit(root: Path) -> str:
    try:
        return subprocess.run(  # noqa: S603
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def save(report: Report, results_dir: Path) -> Path:
    stamp = report.date.replace(":", "").replace("-", "")[:15]
    path = results_dir / report.app / f"{stamp}-{report.commit}-{report.provider['kind']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_json(), indent=1, sort_keys=True, default=str) + "\n")
    return path


def publish(report: Report, results_dir: Path) -> Path:
    """The agent card's eval line (Step 32 reads ``evals/results/<agent>.json``). Refuses runs
    that didn't use real models: a card must not show mock results as the agent's quality."""
    if not report.baseline_eligible:
        raise ValueError(
            f"a {report.provider.get('kind')} run can't be published: only real-model runs "
            "(provider gateway) are cited on cards or used as a baseline"
        )
    agg = report.aggregates()
    path = results_dir / f"{report.app}.json"
    path.write_text(
        json.dumps(
            {
                "passed": agg["succeeded"],
                "total": agg["tasks"],
                "dataset": report.dataset["path"],
                "model": ", ".join(sorted(set(report.provider.get("models", {}).values()))) or "?",
                "date": report.date[:10],
                "report": str(save(report, results_dir).relative_to(results_dir.parent)),
            },
            indent=1,
            sort_keys=True,
        )
        + "\n"
    )
    return path


def compare(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """What changed from report ``a`` to report ``b``: versions, aggregates, and the tasks that
    went from passing to failing (regressions) or the other way."""
    if a["dataset"]["sha256"] != b["dataset"]["sha256"]:
        note = "the task sets differ; per-task changes cover the tasks both runs share"
    else:
        note = ""
    before = {r["task"]: r["success"] for r in a["results"]}
    after = {r["task"]: r["success"] for r in b["results"]}
    shared = sorted(set(before) & set(after))
    changed_versions = {
        k: {"from": a["versions"].get(k), "to": b["versions"].get(k)}
        for k in sorted(set(a["versions"]) | set(b["versions"]))
        if a["versions"].get(k) != b["versions"].get(k)
    }
    ma, mb = a["aggregates"]["metrics"], b["aggregates"]["metrics"]
    return {
        "note": note,
        "versions": changed_versions,
        "success_rate": {
            "from": a["aggregates"]["success_rate"],
            "to": b["aggregates"]["success_rate"],
        },
        "metrics": {
            m: {"from": ma.get(m, {}).get("mean"), "to": mb.get(m, {}).get("mean")}
            for m in sorted(set(ma) | set(mb))
        },
        "regressions": [t for t in shared if before[t] and not after[t]],
        "fixed": [t for t in shared if not before[t] and after[t]],
        "providers": {"from": a["provider"].get("kind"), "to": b["provider"].get("kind")},
    }
