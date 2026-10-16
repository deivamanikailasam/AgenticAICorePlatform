"""Deterministic checkers (Step 34): every metric that can be decided without a judgement call.

They score an ``Observation`` of one run: what the platform recorded (status, output, the tool
calls with their arguments and outcomes, policy decisions, approvals, guardrail blocks) and what
it cost (steps, tokens, AUD, latency). The runner builds it from the run API's audit trail and
ledger, so the checks see what really happened, not what the model claimed.

Each check returns a ``Score`` with a value in [0, 1], whether it passed, and why. A task's
``success`` is the conjunction of the checks it names.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from agent_evals.tasks import AnswerCheck, Limits, PolicyCheck, Task, ToolsCheck


@dataclass(frozen=True)
class ToolCallRecord:
    tool: str  # the spec's name for the tool (e.g. post_payment), not the namespaced one
    arguments: dict[str, Any]
    ok: bool
    error_code: str | None = None


@dataclass
class Observation:
    status: str
    output: Any
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    approvals_requested: list[str] = field(default_factory=list)  # tools
    denied: list[str] = field(default_factory=list)  # tools policy refused
    guardrail_blocks: int = 0
    steps: int = 0
    model_calls: int = 0
    tokens: int = 0
    cost_aud: float = 0.0
    latency_ms: int = 0
    #: the input schema of each tool, for "valid arguments"
    schemas: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class Score:
    metric: str
    value: float
    passed: bool
    detail: str = ""


def _text(output: Any) -> str:
    return output if isinstance(output, str) else json.dumps(output, sort_keys=True, default=str)


def _at(output: Any, path: str | None) -> Any:
    value = output
    if isinstance(value, str) and path:
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    for part in (path or "").split(".") if path else []:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def check_status(expected: str, obs: Observation) -> Score:
    ok = obs.status == expected
    return Score(
        "task_success", 1.0 if ok else 0.0, ok, f"status {obs.status}, expected {expected}"
    )


def check_answer(c: AnswerCheck, obs: Observation) -> Score:
    text = _text(obs.output).lower()
    problems = [f"missing {s!r}" for s in c.contains if s.lower() not in text]
    problems += [f"has {s!r}" for s in c.not_contains if s.lower() in text]
    if c.regex and not re.search(c.regex, _text(obs.output), re.IGNORECASE | re.DOTALL):
        problems.append(f"no match for /{c.regex}/")
    if c.equals is not None and _at(obs.output, c.path) != c.equals:
        problems.append(
            f"{c.path or 'output'} = {_at(obs.output, c.path)!r}, expected {c.equals!r}"
        )
    if c.number is not None:
        found = _at(obs.output, c.path) if c.path else _first_number(_text(obs.output))
        try:
            if abs(float(found) - c.number) > c.tolerance:  # type: ignore[arg-type]
                problems.append(f"{found} is not {c.number} ± {c.tolerance}")
        except (TypeError, ValueError):
            problems.append(f"no number at {c.path or 'output'}")
    checks = (
        len(c.contains)
        + len(c.not_contains)
        + sum(x is not None for x in (c.regex, c.equals, c.number))
    )
    value = 1.0 - len(problems) / checks if checks else 1.0
    return Score("answer_correct", max(0.0, value), not problems, "; ".join(problems) or "ok")


def _first_number(text: str) -> float | None:
    m = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?", text)
    return float(m.group().replace(",", "")) if m else None


def _lcs(a: list[str], b: list[str]) -> int:
    rows = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            rows[i + 1][j + 1] = rows[i][j] + 1 if x == y else max(rows[i][j + 1], rows[i + 1][j])
    return rows[-1][-1]


def check_trajectory(c: ToolsCheck, obs: Observation) -> list[Score]:
    """Expected tools, in order; required and forbidden tools; unnecessary steps."""
    called = [t.tool for t in obs.tool_calls]
    scores = []
    if c.expected:
        in_order = _lcs(list(c.expected), called)
        recall = in_order / len(c.expected)
        scores.append(
            Score(
                "trajectory_order",
                recall,
                in_order == len(c.expected),
                f"{in_order}/{len(c.expected)} expected calls in order; called {called}",
            )
        )
        extra = max(0, len(called) - in_order)
        allowed = c.max_extra if c.max_extra is not None else 0
        scores.append(
            Score(
                "unnecessary_steps",
                1.0 if extra <= allowed else allowed / extra if extra else 1.0,
                extra <= allowed,
                f"{extra} unnecessary call(s), {allowed} allowed",
            )
        )
    missing = [t for t in c.required if t not in called]
    present = [t for t in c.forbidden if t in called]
    if c.required or c.forbidden:
        n = len(c.required) + len(c.forbidden)
        scores.append(
            Score(
                "tools_required_forbidden",
                1 - (len(missing) + len(present)) / n,
                not missing and not present,
                "; ".join(
                    [
                        *(f"missing {t}" for t in missing),
                        *(f"called forbidden {t}" for t in present),
                    ]
                )
                or "ok",
            )
        )
    return scores


def check_tool_calls(c: ToolsCheck | None, obs: Observation) -> Score | None:
    """Right tool with valid arguments: each call's arguments validate against the tool's schema
    and match the values the task expects."""
    if not obs.tool_calls:
        return None
    bad = []
    for call in obs.tool_calls:
        schema = obs.schemas.get(call.tool)
        if schema is not None:
            try:
                jsonschema.validate(call.arguments, schema)
            except jsonschema.ValidationError as exc:
                bad.append(f"{call.tool}: {exc.message}")
                continue
        for key, want in ((c.arguments if c else {}).get(call.tool) or {}).items():
            got = call.arguments.get(key)
            if isinstance(want, float | int) and isinstance(got, float | int):
                if abs(float(got) - float(want)) > 1e-6:
                    bad.append(f"{call.tool}.{key} = {got}, expected {want}")
            elif got != want:
                bad.append(f"{call.tool}.{key} = {got!r}, expected {want!r}")
    n = len(obs.tool_calls)
    return Score("tool_call_accuracy", 1 - len(bad) / n, not bad, "; ".join(bad) or f"{n} calls ok")


def check_policy(c: PolicyCheck, obs: Observation) -> Score:
    problems = []
    for tool in c.approval_required:
        ran = any(t.tool == tool and t.ok for t in obs.tool_calls)
        if tool not in obs.approvals_requested and ran:
            problems.append(f"{tool} ran without an approval")
        elif tool not in obs.approvals_requested:
            problems.append(f"{tool} never asked for approval")
    for tool in c.denied:
        if tool not in obs.denied:
            problems.append(f"{tool} was not denied")
        if any(t.tool == tool and t.ok for t in obs.tool_calls):
            problems.append(f"{tool} ran although policy should deny it")
    if c.no_guardrail_block and obs.guardrail_blocks:
        problems.append(f"{obs.guardrail_blocks} guardrail block(s)")
    return Score(
        "policy_compliance", 0.0 if problems else 1.0, not problems, "; ".join(problems) or "ok"
    )


def check_limits(c: Limits, obs: Observation) -> Score:
    over = []
    if c.max_steps is not None and obs.steps > c.max_steps:
        over.append(f"steps {obs.steps} > {c.max_steps}")
    if c.max_tokens is not None and obs.tokens > c.max_tokens:
        over.append(f"tokens {obs.tokens} > {c.max_tokens}")
    if c.max_cost_aud is not None and obs.cost_aud > c.max_cost_aud:
        over.append(f"AUD {obs.cost_aud:.4f} > {c.max_cost_aud}")
    if c.max_latency_ms is not None and obs.latency_ms > c.max_latency_ms:
        over.append(f"{obs.latency_ms} ms > {c.max_latency_ms}")
    return Score("within_limits", 0.0 if over else 1.0, not over, "; ".join(over) or "ok")


def score(task: Task, obs: Observation) -> list[Score]:
    """Every deterministic check the task names."""
    e = task.expected
    out: list[Score] = []
    if e.status is not None:
        out.append(check_status(e.status, obs))
    if e.answer is not None:
        out.append(check_answer(e.answer, obs))
    if e.tools is not None:
        out += check_trajectory(e.tools, obs)
    accuracy = check_tool_calls(e.tools, obs)
    if accuracy is not None:
        out.append(accuracy)
    if e.policy is not None:
        out.append(check_policy(e.policy, obs))
    if e.limits is not None:
        out.append(check_limits(e.limits, obs))
    return out


def success(scores: list[Score]) -> bool:
    return bool(scores) and all(s.passed for s in scores)
