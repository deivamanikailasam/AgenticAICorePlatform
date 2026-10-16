"""Red-team suite (Step 35): attacks mapped to the OWASP Top 10 for Agentic Applications
(ASI01-ASI10), findings, and the release gate.

``evals/redteam/catalogue.yaml`` is the register:

* ``categories`` — the ten ASI risks with the platform controls that answer each (the spec's
  threat-model table);
* ``tests`` — existing tests that prove a control (``node`` is a pytest node id), each with the
  ASI it covers, the attack it represents, a ``severity`` (what a failure would mean) and its
  ``tier`` (``unit``, ``integration``, ``sandbox``; where it can run).

``evals/redteam/attacks/*.yaml`` are scenario attacks: an attacker's payload put into a real app
through one ``vector`` (user input, a tool result, a retrieved document, memory, a peer agent),
run through the run API with the obedient adversary as the model and the platform's real policy
engine, and judged with the golden-task checks (``expected``): what holding the line looks like.

Every executed test becomes a ``Result``; a failed or erroring one is a ``Finding`` with a stable
``fingerprint`` (the attack id), so the same weakness is tracked as one issue across runs. The
release gate refuses a release while any high or critical finding is open.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

from agent_evals.tasks import Expected, Task

ASI = Literal[
    "ASI01", "ASI02", "ASI03", "ASI04", "ASI05", "ASI06", "ASI07", "ASI08", "ASI09", "ASI10"
]
ALL_ASI: tuple[str, ...] = tuple(f"ASI{i:02d}" for i in range(1, 11))
Severity = Literal["critical", "high", "medium", "low"]
BLOCKING: frozenset[str] = frozenset({"critical", "high"})
Tier = Literal["unit", "integration", "sandbox", "microvm"]
Vector = Literal[
    "user_input", "tool_result", "retrieved_doc", "memory", "peer_agent", "approval", "config"
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Category(_Model):
    name: str
    controls: tuple[str, ...]
    steps: tuple[int, ...] = ()


class LinkedTest(_Model):
    """An existing test that proves a control; run by the suite as one attack."""

    id: str
    asi: ASI
    title: str
    severity: Severity
    node: str  # pytest node id
    tier: Tier = "unit"


class Attack(_Model):
    """A scenario attack through the run API."""

    id: str
    asi: ASI
    title: str
    severity: Severity
    vector: Vector
    technique: str
    app: str
    input: dict[str, Any]
    expected: Expected
    approve: bool = False  # whether the approver says yes if the run asks
    #: an attack that must reach the model to mean anything: the obedient model must see this
    #: text, unless a guardrail stopped it (then the guardrail is the defence that worked)
    payload_marker: str | None = None
    tier: Tier = "integration"

    def task(self) -> Task:
        return Task(
            id=self.id,
            app=self.app,
            input=self.input,
            expected=self.expected,
            tags=(self.asi, self.vector),
            origin=f"red team: {self.technique}",
        )


class Catalogue(_Model):
    categories: dict[ASI, Category]
    tests: tuple[LinkedTest, ...] = ()
    attacks: tuple[Attack, ...] = ()

    @staticmethod
    def load(root: str | Path) -> Catalogue:
        root = Path(root)
        doc = yaml.safe_load((root / "catalogue.yaml").read_text()) or {}
        attacks: list[dict[str, Any]] = []
        for path in sorted((root / "attacks").glob("*.yaml")):
            attacks += yaml.safe_load(path.read_text()) or []
        cat = Catalogue.model_validate({**doc, "attacks": attacks})
        ids = [x.id for x in cat.entries()]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate red-team ids: {', '.join(dupes)}")
        return cat

    def entries(self) -> list[LinkedTest | Attack]:
        return [*self.tests, *self.attacks]

    def by_id(self) -> dict[str, LinkedTest | Attack]:
        return {e.id: e for e in self.entries()}

    def coverage(self, tiers: Iterable[str] | None = None) -> dict[str, list[str]]:
        """Automated tests per ASI category (optionally only those of some tiers)."""
        wanted = set(tiers) if tiers is not None else None
        out: dict[str, list[str]] = {a: [] for a in ALL_ASI}
        for e in self.entries():
            if wanted is None or e.tier in wanted:
                out[e.asi].append(e.id)
        return out

    def gaps(self, tiers: Iterable[str] | None = None) -> list[str]:
        return [asi for asi, ids in self.coverage(tiers).items() if not ids]


Outcome = Literal["held", "finding", "not_run"]


@dataclass
class Result:
    id: str
    asi: str
    title: str
    severity: str
    outcome: Outcome
    detail: str = ""
    source: str = "suite"  # suite | promptfoo | garak
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(f"{self.source}:{self.id}".encode()).hexdigest()[:16]


@dataclass
class Report:
    """One red-team run: every attack's outcome, coverage per ASI, and the findings."""

    commit: str
    date: str
    policy_engine: str
    results: list[Result]
    tiers: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)

    def findings(self) -> list[Result]:
        return [r for r in self.results if r.outcome == "finding"]

    def blocking(self) -> list[Result]:
        return [r for r in self.findings() if r.severity in BLOCKING]

    def coverage(self) -> dict[str, dict[str, int]]:
        out = {a: {"held": 0, "finding": 0, "not_run": 0} for a in ALL_ASI}
        for r in self.results:
            if r.asi in out:
                out[r.asi][r.outcome] += 1
        return out

    def untested(self) -> list[str]:
        """ASI categories without a single executed test in this run."""
        return [a for a, c in self.coverage().items() if c["held"] + c["finding"] == 0]

    def to_json(self) -> dict[str, Any]:
        return {
            "commit": self.commit,
            "date": self.date,
            "policy_engine": self.policy_engine,
            "tiers": list(self.tiers),
            "notes": self.notes,
            "summary": {
                "executed": sum(1 for r in self.results if r.outcome != "not_run"),
                "held": sum(1 for r in self.results if r.outcome == "held"),
                "findings": len(self.findings()),
                "blocking": len(self.blocking()),
                "untested": self.untested(),
            },
            "coverage": self.coverage(),
            "results": [asdict(r) | {"fingerprint": r.fingerprint} for r in self.results],
        }

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), indent=1, default=str) + "\n")
        return path

    @staticmethod
    def load(path: str | Path) -> Report:
        doc = json.loads(Path(path).read_text())
        results = [
            Result(**{k: v for k, v in r.items() if k != "fingerprint"}) for r in doc["results"]
        ]
        return Report(
            doc["commit"],
            doc["date"],
            doc["policy_engine"],
            results,
            tuple(doc.get("tiers", ())),
            list(doc.get("notes", [])),
        )

    def summary(self) -> str:
        s = self.to_json()["summary"]
        lines = [
            f"red team: {s['executed']} attacks executed, {s['held']} held, "
            f"{s['findings']} finding(s), {s['blocking']} blocking (policy engine: "
            f"{self.policy_engine}; tiers: {', '.join(self.tiers) or 'all'})"
        ]
        for asi, c in self.coverage().items():
            lines.append(
                f"  {asi}: held {c['held']}, findings {c['finding']}, not run {c['not_run']}"
            )
        for r in self.findings():
            lines.append(f"  FINDING [{r.severity}] {r.asi} {r.id}: {r.detail[:300]}")
        lines += [f"  note: {n}" for n in self.notes]
        return "\n".join(lines)


def merge(reports: Sequence[Report]) -> Report:
    """Several runs (e.g. the integration and sandbox tiers) as one report; a test executed in
    any run counts as executed, and a finding in any run is a finding."""
    by_id: dict[str, Result] = {}
    rank = {"not_run": 0, "held": 1, "finding": 2}
    for rep in reports:
        for r in rep.results:
            known = by_id.get(f"{r.source}:{r.id}")
            if known is None or rank[r.outcome] > rank[known.outcome]:
                by_id[f"{r.source}:{r.id}"] = r
    engines = sorted({r.policy_engine for r in reports})
    return Report(
        reports[0].commit if reports else "",
        max((r.date for r in reports), default=""),
        "+".join(engines),
        list(by_id.values()),
        tuple(sorted({t for r in reports for t in r.tiers})),
        [n for r in reports for n in r.notes],
    )


@dataclass(frozen=True)
class GateDecision:
    release: bool
    reasons: tuple[str, ...]


def gate(
    report: Report | None,
    open_issues: Sequence[Mapping[str, Any]] = (),
    require_all_categories: bool = True,
) -> GateDecision:
    """Release only with a red-team report that covers every ASI category, has no high or
    critical finding, and no open high or critical red-team issue (fixed in code but not yet
    re-verified counts as open until a run closes it)."""
    reasons = []
    if report is None:
        return GateDecision(False, ("no red-team report for this commit",))
    for r in report.blocking():
        reasons.append(f"{r.severity} finding {r.asi} {r.id}: {r.detail[:200]}")
    if require_all_categories and report.untested():
        reasons.append(f"no executed test for {', '.join(report.untested())}")
    for issue in open_issues:
        labels = {str(x) for x in issue.get("labels", ())}
        if "redteam" in labels and labels & {f"severity:{s}" for s in BLOCKING}:
            reasons.append(f"open issue #{issue.get('number')}: {issue.get('title')}")
    return GateDecision(not reasons, tuple(reasons))


__all__ = [
    "ALL_ASI",
    "BLOCKING",
    "Attack",
    "Catalogue",
    "Category",
    "GateDecision",
    "LinkedTest",
    "Report",
    "Result",
    "gate",
    "merge",
]
