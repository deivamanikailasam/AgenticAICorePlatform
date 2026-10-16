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

from agent_evals.tasks import (
    AnswerCheck,
    ConversationCheck,
    Limits,
    PolicyCheck,
    Task,
    TeamCheck,
    ToolsCheck,
)


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
    approval_arguments: dict[str, dict[str, Any]] = field(default_factory=dict)  # tool -> args
    denied: list[str] = field(default_factory=list)  # tools policy refused
    delegations: list[str] = field(default_factory=list)  # workers, in order (policy-allowed)
    guardrail_blocks: int = 0
    steps: int = 0
    model_calls: int = 0
    tokens: int = 0
    cost_aud: float = 0.0
    latency_ms: int = 0
    #: the input schema of each tool, for "valid arguments"
    schemas: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: multi-turn tasks: goal_met, gave_up, turns, patience_left, ended_because, transcript
    conversation: dict[str, Any] | None = None


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


def check_status(expected: str | tuple[str, ...], obs: Observation) -> Score:
    allowed = (expected,) if isinstance(expected, str) else expected
    ok = obs.status in allowed
    want = " or ".join(allowed)
    return Score("task_success", 1.0 if ok else 0.0, ok, f"status {obs.status}, expected {want}")


def answer_of(output: Any, path: str | None = None) -> Any:
    """What text checks look at: the value at ``path``, else a team's ``answer`` (team results
    also carry rounds and costs, which aren't the answer), else the whole output."""
    if path:
        return _at(output, path)
    if isinstance(output, dict) and "answer" in output:
        return output["answer"]
    return output


def check_answer(c: AnswerCheck, obs: Observation) -> Score:
    answer = _text(answer_of(obs.output, c.path if c.equals is None and c.number is None else None))
    text = answer.lower()
    problems = [f"missing {s!r}" for s in c.contains if s.lower() not in text]
    problems += [f"has {s!r}" for s in c.not_contains if s.lower() in text]
    if c.regex and not re.search(c.regex, answer, re.IGNORECASE | re.DOTALL):
        problems.append(f"no match for /{c.regex}/")
    if c.equals is not None and _at(obs.output, c.path) != c.equals:
        problems.append(
            f"{c.path or 'output'} = {_at(obs.output, c.path)!r}, expected {c.equals!r}"
        )
    if c.number is not None:
        found = _at(obs.output, c.path) if c.path else _first_number(answer)
        try:
            if abs(float(found) - c.number) > c.tolerance:  # type: ignore[arg-type]
                problems.append(f"{found} is not {c.number} ± {c.tolerance}")
        except (TypeError, ValueError):
            problems.append(f"no number at {c.path or 'output'}")
    if c.max_words is not None:
        words = len(answer.split())
        if words > c.max_words:
            problems.append(f"{words} words, at most {c.max_words}")
    checks = (
        len(c.contains)
        + len(c.not_contains)
        + sum(x is not None for x in (c.regex, c.equals, c.number, c.max_words))
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
    for tool, want in c.approval_arguments.items():
        got = obs.approval_arguments.get(tool)
        if got is None:
            continue  # reported above if the approval was required
        for key, value in want.items():
            if isinstance(value, int | float) and isinstance(got.get(key), int | float):
                if abs(float(got[key]) - float(value)) > 1e-6:
                    problems.append(f"approval asks {tool}.{key} = {got[key]}, expected {value}")
            elif got.get(key) != value:
                problems.append(
                    f"approval asks {tool}.{key} = {got.get(key)!r}, expected {value!r}"
                )
    for tool in c.not_requested:
        if tool in obs.approvals_requested:
            problems.append(f"{tool} reached an approval request although it must not")
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


def check_team(c: TeamCheck, obs: Observation) -> Score:
    seen = obs.delegations
    problems = [f"never delegated to {w}" for w in c.workers_required if w not in seen]
    problems += [f"delegated to {w}" for w in c.workers_forbidden if w in seen]
    if c.first_worker is not None and (not seen or seen[0] != c.first_worker):
        problems.append(f"first worker {seen[0] if seen else 'none'}, expected {c.first_worker}")
    for worker, n in c.counts.items():
        if seen.count(worker) != n:
            problems.append(f"{worker} delegated {seen.count(worker)} time(s), expected {n}")
    if c.max_delegations is not None and len(seen) > c.max_delegations:
        problems.append(f"{len(seen)} delegations, at most {c.max_delegations}")
    return Score(
        "team_trajectory", 0.0 if problems else 1.0, not problems, "; ".join(problems) or "ok"
    )


def check_limits(c: Limits, obs: Observation) -> Score:
    over = []
    if c.max_steps is not None and obs.steps > c.max_steps:
        over.append(f"steps {obs.steps} > {c.max_steps}")
    if c.max_model_calls is not None and obs.model_calls > c.max_model_calls:
        over.append(f"model calls {obs.model_calls} > {c.max_model_calls}")
    if c.max_tokens is not None and obs.tokens > c.max_tokens:
        over.append(f"tokens {obs.tokens} > {c.max_tokens}")
    if c.max_cost_aud is not None and obs.cost_aud > c.max_cost_aud:
        over.append(f"AUD {obs.cost_aud:.4f} > {c.max_cost_aud}")
    if c.max_latency_ms is not None and obs.latency_ms > c.max_latency_ms:
        over.append(f"{obs.latency_ms} ms > {c.max_latency_ms}")
    return Score("within_limits", 0.0 if over else 1.0, not over, "; ".join(over) or "ok")


def check_conversation(c: ConversationCheck, obs: Observation) -> Score:
    convo = obs.conversation
    if convo is None:
        return Score("goal_achieved", 0.0, False, "not a simulated conversation")
    problems = []
    if bool(convo.get("goal_met")) != c.goal_met:
        problems.append(
            f"goal {'not ' if c.goal_met else ''}met ({convo.get('ended_because', '')})"
        )
    turns = int(convo.get("turns", 0))
    if c.max_turns is not None and turns > c.max_turns:
        problems.append(f"{turns} turns, at most {c.max_turns}")
    return Score(
        "goal_achieved",
        0.0 if problems else 1.0,
        not problems,
        "; ".join(problems) or f"{convo.get('ended_because')} in {turns} turn(s)",
    )


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
    if e.team is not None:
        out.append(check_team(e.team, obs))
    if e.limits is not None:
        out.append(check_limits(e.limits, obs))
    if e.conversation is not None:
        out.append(check_conversation(e.conversation, obs))
    return out


def success(scores: list[Score]) -> bool:
    return bool(scores) and all(s.passed for s in scores)
