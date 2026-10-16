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


def test_calibration_reports_not_measured_without_human_labels(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from agent_contracts.dummies.models import ScriptedModelClient
    from agent_contracts.models import Message, Role

    import scripts.eval_app as cli

    monkeypatch.setattr(cli, "RESULTS", tmp_path)
    monkeypatch.setattr(cli, "ROOT", ROOT)
    judge = ScriptedModelClient(
        [Message(role=Role.ASSISTANT, content='{"label": "pass", "reason": "ok"}')]
    )
    labels = ROOT / "evals" / "labels" / "invoice-resolution.jsonl"
    assert not labels.exists(), "no human labels have been collected (this test checks that path)"
    assert cli.calibrate_report(report(tmp_path, True), "invoice-resolution", judge) == 0
    saved = json.loads((tmp_path / "calibration" / "invoice-resolution.json").read_text())
    assert saved["measured"] is False
    assert saved["agreement"] is None
    assert "not measured" in saved["summary"]
    assert cli.calibrate_report(report(tmp_path, False), "invoice-resolution", judge) == 2
