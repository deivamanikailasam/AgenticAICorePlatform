"""Attack-library results as red-team findings (Step 35): promptfoo and garak.

Both run real attacks against the run API (``evals/redteam/libraries/target.py``); their results
are imported here into the same ``Result`` shape as the suite's own attacks, so they share the
report, the issue tracking and the release gate.

* **promptfoo**: one result per test case; its metadata gives the id, ASI category and severity.
  A failed assertion is a finding; a case that could not run (provider error) is a finding too,
  since an attack that didn't run proves nothing.
* **garak**: one result per probe, from the report's ``eval`` entries (every detector of the
  probe); any detector hit is a finding. ``evals/redteam/libraries/mapping.yaml`` maps the probe
  family to an ASI category and a severity. Counts in the detail are the run's own (attempts
  evaluated and hits), never estimates.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from agent_evals.redteam import Result


def from_promptfoo(doc: Mapping[str, Any]) -> list[Result]:
    out = []
    for row in (doc.get("results") or {}).get("results") or []:
        case = row.get("testCase") or {}
        meta = case.get("metadata") or {}
        rid = str(meta.get("id") or case.get("description") or row.get("id"))
        response = row.get("response") or {}
        output = str(response.get("output") or "")[:300]
        error = row.get("error") or response.get("error")
        grading = row.get("gradingResult") or {}
        if error:
            outcome, detail = "finding", f"attack could not run: {str(error)[:300]}"
        elif row.get("success"):
            outcome, detail = "held", "all assertions passed"
        else:
            outcome, detail = "finding", f"{grading.get('reason', 'assertion failed')}: {output}"
        out.append(
            Result(
                id=rid,
                asi=str(meta.get("asi", "ASI01")),
                title=str(case.get("description") or rid),
                severity=str(meta.get("severity", "high")),
                outcome=outcome,  # type: ignore[arg-type]
                detail=detail,
                source="promptfoo",
                evidence={"output": output, "attack": (case.get("vars") or {}).get("attack")},
            )
        )
    return out


def from_garak(
    lines: Iterable[str],
    mapping: Mapping[str, Mapping[str, str]],
    expected: Iterable[str] = (),
) -> list[Result]:
    """``expected``: the probes the run was configured with; one that left no result (the run
    stopped early, the probe failed to load) is a finding, never silently missing."""
    evals: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in lines:
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("entry_type") == "eval":
            evals[str(entry["probe"])].append(entry)
    out = []
    for probe, rows in sorted(evals.items()):
        family = probe.split(".", 1)[0]
        info = mapping.get(family) or {"asi": "ASI01", "severity": "high", "title": probe}
        evaluated = max(int(r.get("total_evaluated", 0)) for r in rows)
        hits = {str(r["detector"]): int(r.get("fails", 0)) for r in rows}
        worst = max(hits.values(), default=0)
        detail = (
            f"{worst} of {evaluated} attempts hit ("
            + ", ".join(f"{d}: {n}" for d, n in hits.items())
            + ")"
        )
        out.append(
            Result(
                id=f"garak-{probe}",
                asi=str(info["asi"]),
                title=f"{info['title']}: {probe}",
                severity=str(info["severity"]),
                outcome="finding" if worst or not evaluated else "held",
                detail=detail if evaluated else "no attempt was evaluated",
                source="garak",
                evidence={"evaluated": evaluated, "hits": hits},
            )
        )
    for probe in sorted(set(expected) - set(evals)):
        info = mapping.get(probe.split(".", 1)[0]) or {"asi": "ASI01", "severity": "high"}
        out.append(
            Result(
                id=f"garak-{probe}",
                asi=str(info["asi"]),
                title=f"{info.get('title', probe)}: {probe}",
                severity=str(info["severity"]),
                outcome="finding",
                detail="the probe did not complete: no result in the report",
                source="garak",
            )
        )
    return out


def garak_probes(config: Mapping[str, Any]) -> list[str]:
    """The probes a garak config selects (``run.spec.include`` entries ``probes.<name>``)."""
    include = ((config.get("run") or {}).get("spec") or {}).get("include") or []
    return [str(p).removeprefix("probes.") for p in include if str(p).startswith("probes.")]


__all__ = ["from_garak", "from_promptfoo", "garak_probes"]
