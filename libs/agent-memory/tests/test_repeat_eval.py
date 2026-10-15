"""The repeat-task memory eval runs offline and memory beats no memory without leaking."""

from pathlib import Path

from agent_memory.repeat_eval import evaluate, render

DATASET = Path(__file__).resolve().parents[3] / "evals/golden/memory-repeat"


async def test_memory_improves_repeat_tasks_without_cross_user_leaks() -> None:
    report = await evaluate(DATASET)
    without, with_memory = report["modes"]["no memory"], report["modes"]["memory"]
    assert report["cases"] == 10
    assert without["correct"] == 0, "nothing carries over between sessions without memory"
    assert with_memory["correct"] > without["correct"]
    assert with_memory["leaks"] == 0
    assert "10 cases" in render(report)
