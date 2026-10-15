"""Guardrail eval suite (Step 27 "Done when").

Each labelled case runs through the platform's guardrail pipeline at its stage, with the eval
configuration below (the spec-level settings a business agent would use). A case passes when the
outcome is what it is labelled: ``block``, ``redact`` or ``allow``. Tool-description cases go
through the MCP screen (the same check that keeps a poisoned tool from being registered).

Cases that need Presidio run when ``P0_PRESIDIO_ANALYZER_URL`` is set and are reported as
skipped otherwise. The classifier detector joins when ``AGENT_INJECTION_CLASSIFIER_URL`` is set.

    uv run python -m agent_guardrails.eval evals/golden/guardrails
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_contracts.interfaces import Guardrail
from agent_contracts.models import GuardrailStage, ToolSpec, Trust

from agent_guardrails.checks import (
    OutputGuardrail,
    SafetyFilterGuardrail,
    ToolArgsGuardrail,
    TopicFilterGuardrail,
)
from agent_guardrails.guarded import GuardrailRuntime
from agent_guardrails.injection import PromptInjectionGuardrail
from agent_guardrails.pii import PresidioPiiGuardrail
from agent_guardrails.pipeline import GuardrailPipeline
from agent_guardrails.secrets import SecretLeakGuardrail

PAY_SCHEMA = {
    "type": "object",
    "properties": {"amount_aud": {"type": "number"}, "currency": {"type": "string"}},
    "required": ["amount_aud"],
}


def guardrails(with_presidio: bool) -> list[Guardrail]:
    gs: list[Guardrail] = [
        PromptInjectionGuardrail(),
        SecretLeakGuardrail(),
        ToolArgsGuardrail({"pay": {"amount_aud": {"max": 5000}, "currency": {"enum": ["AUD"]}}}),
        OutputGuardrail(forbidden=[r"\bguarantee(d)?\b"]),
        TopicFilterGuardrail({"investment_advice": ["stock tips?", "which shares"]}),
        SafetyFilterGuardrail(),
    ]
    if with_presidio:
        gs.append(PresidioPiiGuardrail())
    return gs


async def run_case(rt: GuardrailRuntime, case: dict[str, Any]) -> str:
    if case["stage"] == "tool_description":
        spec = ToolSpec(
            name=case["tool"], description=case["text"], input_schema={"type": "object"}
        )
        return "block" if await rt.screen_tool(spec) else "allow"
    stage = GuardrailStage(case["stage"])
    trust = Trust.UNTRUSTED if case["trust"] == "untrusted" else Trust.USER
    data: dict[str, Any] = {}
    text = case["text"]
    if stage is GuardrailStage.TOOL_ARGS:
        data = {
            "arguments": case["arguments"],
            "schema": PAY_SCHEMA if case["tool"] == "pay" else None,
        }
        text = json.dumps(case["arguments"], sort_keys=True)
    if stage is GuardrailStage.OUTPUT:
        data = {"output": text}
    outcome = await rt.pipeline.run(stage, text, data=data, tool=case.get("tool"), trust=trust)
    if outcome.blocked:
        return "block"
    return "redact" if outcome.text != text else "allow"


async def evaluate(directory: str | Path) -> dict[str, Any]:
    cases = [
        json.loads(x)
        for x in (Path(directory) / "cases.jsonl").read_text().splitlines()
        if x.strip()
    ]
    presidio = bool(os.environ.get("P0_PRESIDIO_ANALYZER_URL"))
    rt = GuardrailRuntime(GuardrailPipeline(guardrails(presidio)))
    rows = []
    for case in cases:
        if case.get("needs") == "presidio" and not presidio:
            rows.append({**case, "got": "skipped", "passed": None})
            continue
        got = await run_case(rt, case)
        rows.append({**case, "got": got, "passed": got == case["expect"]})
    return {
        "dataset": str(directory),
        "cases": len(cases),
        "detectors": [d.name for d in PromptInjectionGuardrail().detectors],
        "presidio": presidio,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "rows": rows,
    }


def render(report: dict[str, Any]) -> str:
    rows = report["rows"]
    ran = [r for r in rows if r["passed"] is not None]
    by_cat: dict[str, Counter[str]] = {}
    for r in ran:
        c = by_cat.setdefault(r["category"], Counter())
        c["total"] += 1
        c["passed"] += int(r["passed"])
    injection = ("direct_injection", "indirect_document", "mcp_tool_description")
    attacks = [r for r in ran if r["category"] in injection and r["expect"] == "block"]
    benign = [r for r in ran if r["expect"] == "allow"]
    lines = [
        f"Guardrail eval · {report['dataset']} · {report['cases']} cases ({len(ran)} run, "
        f"{len(rows) - len(ran)} skipped) · injection detectors: {', '.join(report['detectors'])} · "
        f"Presidio: {'yes' if report['presidio'] else 'no'} · {report['at']}",
        "",
        f"Injection attacks blocked: {sum(r['passed'] for r in attacks)}/{len(attacks)} · "
        f"benign cases allowed (no false positive): {sum(r['passed'] for r in benign)}/{len(benign)} · "
        f"all cases: {sum(r['passed'] for r in ran)}/{len(ran)}",
        "",
        "| Category | Passed |",
        "|---|---|",
        *[f"| {cat} | {c['passed']}/{c['total']} |" for cat, c in sorted(by_cat.items())],
    ]
    failed = [r for r in ran if not r["passed"]]
    if failed:
        lines += [
            "",
            "Failed:",
            *[
                f"- {r['id']}: expected {r['expect']}, got {r['got']} — {r['text'][:70]!r}"
                for r in failed
            ],
        ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Guardrail eval suite")
    p.add_argument("dataset", nargs="?", default="evals/golden/guardrails")
    args = p.parse_args(argv)
    report = asyncio.run(evaluate(args.dataset))
    print(render(report))
    return 0 if all(r["passed"] is not False for r in report["rows"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
