"""The guardrail eval suite passes (Step 27 "Done when"), including indirect injection through a
document and through an MCP tool description. Presidio cases run when its URL is set (the
integration job and the Mac)."""

from pathlib import Path

from agent_guardrails.eval import evaluate, render

DATASET = Path(__file__).resolve().parents[3] / "evals" / "golden" / "guardrails"


async def test_every_case_meets_its_label() -> None:
    report = await evaluate(DATASET)
    failed = [r["id"] for r in report["rows"] if r["passed"] is False]
    assert failed == [], render(report)
    categories = {r["category"] for r in report["rows"] if r["passed"]}
    assert {"indirect_document", "mcp_tool_description", "direct_injection"} <= categories
