"""The long-dialogue eval: rolling summaries keep every call in the window with no drop in
task success against the unlimited baseline (Step 21 "Done when", offline stand-in)."""

from pathlib import Path

from agent_runtime.dialogue_eval import evaluate, render

DATASET = Path(__file__).resolve().parents[3] / "evals/golden/long-dialogue"


async def test_rolling_summary_stays_in_the_window_without_losing_task_success() -> None:
    report = await evaluate(DATASET, window=8192)
    s = report["strategies"]
    assert report["turns"] == 1000
    assert report["probes"] == 20
    assert s["unlimited"]["over"] > 0, "the baseline does not fit: the test is meaningful"
    assert s["rolling_summary"]["over"] == 0
    assert s["rolling_summary"]["largest"] <= 8192
    assert s["rolling_summary"]["correct"] >= s["unlimited"]["correct"]
    assert s["trim"]["over"] == 0
    assert "1000 user turns" in render(report)
