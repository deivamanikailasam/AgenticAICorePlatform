"""Step 34: the rubric is well-formed, and labelling sheets are made only from real-model runs."""

import csv
import json
from pathlib import Path

from agent_evals.judge import Rubric

from scripts.eval_label_sheet import ROOT, main


def test_the_committed_rubric_loads() -> None:
    rubric = Rubric.load(ROOT / "evals" / "rubrics" / "invoice-resolution.md")
    assert rubric.labels == ("pass", "fail")
    assert "finance-policy 4.2" in rubric.text


def report(tmp_path: Path, eligible: bool) -> Path:
    path = tmp_path / "r.json"
    path.write_text(
        json.dumps(
            {
                "baseline_eligible": eligible,
                "results": [{"task": "gst-error-INV-1", "status": "succeeded", "output": {"x": 1}}],
            }
        )
    )
    return path


def test_mock_runs_are_not_labelled(tmp_path: Path) -> None:
    assert main([str(report(tmp_path, False)), "invoice-resolution"]) == 2


def test_a_sheet_has_empty_label_columns_and_no_judge_verdicts(tmp_path: Path) -> None:
    sheet = ROOT / "evals" / "labels" / "invoice-resolution-sheet.csv"
    try:
        assert main([str(report(tmp_path, True)), "invoice-resolution"]) == 0
        (row,) = list(csv.DictReader(sheet.open()))
        assert row["item"] == "gst-error-INV-1"
        assert row["label"] == row["labeller"] == ""
        assert "judge" not in "".join(row)
    finally:
        sheet.unlink(missing_ok=True)
