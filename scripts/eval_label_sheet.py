"""Write a labelling sheet for people (Step 34): every item of a report that a rubric applies to,
with the task, the agent's output and the rubric, and empty label columns. The judge's verdicts are
not included, so labellers aren't anchored by them.

    just eval-label-sheet evals/results/invoice-triage/<report>.json invoice-resolution
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str]) -> int:
    report_path, rubric = Path(argv[0]), argv[1]
    report = json.loads(report_path.read_text())
    if not report.get("baseline_eligible"):
        print(
            "refused: label real-model outputs only (this report used a mock provider)",
            file=sys.stderr,
        )
        return 2
    outputs = {r["task"]: r for r in report["results"]}
    rows = [
        {"item": task, "status": r["status"], "output": json.dumps(r.get("output"), default=str)}
        for task, r in sorted(outputs.items())
    ]
    out = ROOT / "evals" / "labels" / f"{rubric}-sheet.csv"
    with out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, ["item", "status", "output", "label", "labeller", "notes"])
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "label": "", "labeller": "", "notes": ""})
    print(f"wrote {len(rows)} items to {out.relative_to(ROOT)}; rubric: evals/rubrics/{rubric}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
