"""Validation gates between chain steps (Step 15): ``config.gate`` on an LLM node.

A gate checks a step's output before the next step sees it::

    gate:
      schema: { type: object, required: [summary] }      # JSON Schema on the output
      checks:                                             # simple declarative checks
        - { path: summary, min_length: 20, max_length: 600 }
        - { path: summary, not_pattern: "(?i)as an ai" }
        - { path: label, one_of: [urgent, normal] }
      fn: my_pkg.gates.no_new_numbers                     # (output, values) -> True | "reason"
      max_retries: 1                                      # re-ask with the reasons (default 1)

Failing output is sent back to the model with the reasons, at most ``max_retries`` times; then
the node fails with ``GateFailedError`` so an ``on_error`` edge can take over. Nothing in a gate
is evaluated as code except the named, installed ``fn``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

import jsonschema

from agent_runtime.executors import lookup

GateFn = Callable[[Any, Mapping[str, Any]], Any]


class GateFailedError(ValueError):
    def __init__(self, node_id: str, reasons: list[str]) -> None:
        super().__init__(f"gate on '{node_id}' failed: {'; '.join(reasons)}")
        self.reasons = reasons


def _check(output: Any, check: Mapping[str, Any]) -> list[str]:
    path = check.get("path")
    try:
        value = lookup(output, str(path)) if path else output
    except KeyError:
        return [f"missing '{path}'"]
    where = f"'{path}'" if path else "output"
    text = value if isinstance(value, str) else None
    problems = []
    if "min_length" in check and (text is None or len(text) < int(check["min_length"])):
        problems.append(f"{where} shorter than {check['min_length']} characters")
    if "max_length" in check and text is not None and len(text) > int(check["max_length"]):
        problems.append(f"{where} longer than {check['max_length']} characters")
    if "pattern" in check and (text is None or not re.search(str(check["pattern"]), text)):
        problems.append(f"{where} does not match /{check['pattern']}/")
    if "not_pattern" in check and text is not None and re.search(str(check["not_pattern"]), text):
        problems.append(f"{where} matches the forbidden /{check['not_pattern']}/")
    if "one_of" in check and value not in check["one_of"]:
        problems.append(f"{where} is {value!r}, not one of {list(check['one_of'])}")
    if "equals" in check and value != check["equals"]:
        problems.append(f"{where} is {value!r}, expected {check['equals']!r}")
    return problems


def gate_reasons(
    gate: Mapping[str, Any],
    output: Any,
    values: Mapping[str, Any],
    functions: Mapping[str, GateFn],
) -> list[str]:
    """Every reason the output fails the gate (empty when it passes)."""
    reasons: list[str] = []
    if gate.get("schema") is not None:
        try:
            jsonschema.validate(output, gate["schema"])
        except jsonschema.ValidationError as exc:
            reasons.append(f"schema: {exc.message}")
    for check in gate.get("checks", ()):
        reasons += _check(output, check)
    if gate.get("fn"):
        verdict = functions[str(gate["fn"])](output, values)
        if verdict is not True:
            reasons.append(str(verdict or f"{gate['fn']} rejected the output"))
    return reasons
