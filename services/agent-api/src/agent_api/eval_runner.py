"""Run a golden task set through the run API and score it (Step 34).

Every task is a real run: ``POST /v1/agents/<agent>/runs`` on an in-process run API, with the
platform's assembly (policy on every tool call, guardrails, budgets, circuits, audit), the app's
real tools, and the chosen model provider. When a run stops for an approval, the runner records
what the person was asked to approve and, if the task says so, approves it as a finance approver
and lets the run finish.

The observation the checkers score is taken from the platform's own records: the audit trail
(tool calls with arguments and outcomes, policy decisions, guardrail results), the run's
interrupts (approval requests and their arguments), the ledger (model calls, tokens, AUD) and
the run's events (steps).
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx
from agent_audit.stamping import agent_versions
from agent_compiler.compiler import compile_spec
from agent_contracts.dummies.observability import MemoryAuditSink
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_contracts.interfaces import ModelClient, PolicyDecisionPoint
from agent_contracts.models import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    ModelCapabilities,
    Usage,
)
from agent_evals.checks import Observation, ToolCallRecord, score, success
from agent_evals.results import Report, TaskResult, git_commit, now
from agent_evals.tasks import Task, dataset_digest, load_tasks
from agent_evals.user_sim import ScriptedUser, UserProfile, converse, render_turn
from agent_runtime.models import GatewayModelClient
from agent_runtime.models.ledger import UsageLedger
from agent_spec import AgentSpec
from agent_spec.platform import PlatformConfig, load_platform
from agent_tools.registry import InMemoryToolRegistry
from mcp.server.auth.provider import AccessToken

from agent_api.app import build_app
from agent_api.assembly import Assembly
from agent_api.runs import RunManager

ROOT = Path(__file__).resolve().parents[4]
REQUESTER, APPROVER = "eval-requester", "eval-approver"
USERS = {
    REQUESTER: ("eval.user", ["/acme/finance"]),
    APPROVER: ("eval.approver", ["/acme/approvers", "/acme/finance-approvers"]),
}


class AppHarness(Protocol):
    """What an app supplies to be evaluated: its spec, tools and any data its tools need."""

    agent: str  # the top-level agent's name

    def spec(self) -> AgentSpec: ...

    def platform(self, base: PlatformConfig) -> PlatformConfig: ...

    async def registry(self, spec: AgentSpec) -> InMemoryToolRegistry: ...

    async def prepare(self, tasks: Sequence[Task]) -> None: ...

    async def close(self) -> None: ...


class _Verifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        if token not in USERS:
            return None
        user, groups = USERS[token]
        return AccessToken(
            token=token,
            client_id="eval",
            scopes=[],
            expires_at=None,
            claims={"preferred_username": user, "groups": groups},
        )


@dataclass
class Provider:
    """Where model calls go: ``kind`` is ``scripted``, ``stand-in`` or ``gateway`` (real)."""

    kind: str
    factory: Callable[[AgentSpec], ModelClient]
    models: dict[str, str] = field(default_factory=dict)


def _spec_name(spec: AgentSpec, ref: str | None, fallback: str) -> str:
    for t in spec.tools:
        if t.ref == ref:
            return t.name
    return fallback


async def _observe(
    manager: RunManager, audit: MemoryAuditSink, run: Any, spec: AgentSpec, started: float
) -> Observation:
    events = [r.event for r in await audit.read() if r.event.ctx.run_id.split("/")[0] == run.run_id]
    calls = [
        ToolCallRecord(
            _spec_name(spec, e.payload.get("ref"), str(e.payload.get("tool"))),
            dict(e.payload.get("arguments") or {}),
            bool(e.payload.get("ok")),
            e.payload.get("error_code"),
        )
        for e in events
        if e.type == "tool.call"
    ]
    denied = [
        _spec_name(spec, e.payload.get("ref"), str(e.payload.get("tool")))
        for e in events
        if e.type == "policy.decision" and e.payload.get("decision") == "deny"
    ]
    blocks = sum(
        1 for e in events if e.type == "guardrail.result" and e.payload.get("action") == "block"
    )
    delegations = [
        str(e.payload.get("worker"))
        for e in events
        if e.type == "agent.delegate" and e.payload.get("effect") == "allow"
    ]
    ledger = manager.assembly.ledger
    rows = ledger.tree(run.run_id)  # the run and its team workers
    usage = sum((r.usage for r in rows), Usage())
    steps = sum(1 for e in run.events if e.get("type") == "STEP_FINISHED")
    output = next(
        (
            e.get("result", {}).get("output")
            for e in reversed(run.events)
            if e.get("type") == "RUN_FINISHED"
        ),
        None,
    )
    return Observation(
        status=run.status,
        output=output,
        tool_calls=calls,
        denied=denied,
        delegations=delegations,
        guardrail_blocks=blocks,
        steps=steps,
        model_calls=len(rows),
        tokens=usage.input_tokens + usage.output_tokens,
        cost_aud=usage.cost_aud,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


class _Metered(ModelClient):
    """A scripted or stand-in model whose reported usage is recorded like a gateway call."""

    def __init__(self, inner: ModelClient, ledger: UsageLedger) -> None:
        self.inner, self.ledger = inner, ledger

    def capabilities(self, model: str) -> ModelCapabilities:
        return self.inner.capabilities(model)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        response = await self.inner.chat(request)
        self.ledger.record(
            request.metadata.get("run_id", "-"), request.model, response.model, response.usage
        )
        return response

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        usage = Usage()
        async for chunk in self.inner.stream(request):
            usage = chunk.usage or usage
            yield chunk
        self.ledger.record(request.metadata.get("run_id", "-"), request.model, request.model, usage)


@dataclass
class Session:
    """An in-process run API over the platform's assembly, for one harness and provider."""

    spec: AgentSpec
    manager: RunManager
    audit: MemoryAuditSink
    client: httpx.AsyncClient
    schemas: dict[str, dict[str, Any]]

    async def start(
        self, inputs: dict[str, Any], thread_id: str | None = None
    ) -> tuple[Any, httpx.Response]:
        body: dict[str, Any] = {"input": inputs}
        if thread_id:
            body["thread_id"] = thread_id
        resp = await self.client.post(
            f"/v1/agents/{self.spec.name}/runs",
            json=body,
            headers={"Authorization": f"Bearer {REQUESTER}"},
        )
        if resp.status_code != 201:
            return None, resp
        run = self.manager.runs[resp.json()["run_id"]]
        await self.manager.wait_idle(run)
        return run, resp


@asynccontextmanager
async def session(
    harness: AppHarness,
    provider: Provider,
    tasks: Sequence[Task] = (),
    pdp: PolicyDecisionPoint | None = None,
) -> AsyncIterator[Session]:
    spec = harness.spec()
    audit = MemoryAuditSink()
    assembly = Assembly(
        harness.platform(load_platform()),
        pdp or RulesPolicyDecisionPoint(),
        audit,
        model_factory=provider.factory,
        registry_factory=harness.registry,
        memory_factory=_no_memory,
    )

    def model_for(agent: AgentSpec) -> ModelClient:
        # usage must land in the assembly's ledger, which the observation reads (model calls,
        # tokens, AUD); a gateway client built by the provider would otherwise keep its own,
        # and a scripted one records none
        model = provider.factory(agent)
        if isinstance(model, GatewayModelClient):
            model.ledger = assembly.ledger
            return model
        return _Metered(model, assembly.ledger)

    assembly.model_factory = model_for
    manager = RunManager({spec.name: spec}, assembly)
    app = build_app(manager, _Verifier())
    schemas: dict[str, dict[str, Any]] = {}
    await harness.prepare(tasks)
    try:
        registry = await harness.registry(spec)
        for tool_spec in await registry.list():
            schemas[_spec_name(spec, tool_spec.ref, tool_spec.name)] = tool_spec.input_schema
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://eval"
        ) as c:
            yield Session(spec, manager, audit, c, schemas)
    finally:
        await harness.close()


def _requested(run: Any) -> dict[str, dict[str, Any]]:
    requested: dict[str, dict[str, Any]] = {}
    for item in run.interrupts:
        action = item.get("action") or {}
        for call in action.get("calls") or [action]:
            if call.get("tool"):
                requested[str(call["tool"])] = dict(call.get("arguments") or {})
    return requested


def _error(run: Any) -> str:
    return next((str(e.get("message")) for e in run.events if e.get("type") == "RUN_ERROR"), "")


async def run_tasks(
    harness: AppHarness,
    tasks: Sequence[Task],
    provider: Provider,
    approve: Callable[[Task], bool] = lambda t: bool(t.input.get("_approve", True)),
    pdp: PolicyDecisionPoint | None = None,
) -> list[tuple[Task, Observation, str]]:
    out: list[tuple[Task, Observation, str]] = []
    async with session(harness, provider, tasks, pdp) as s:
        for task in tasks:
            if task.input.get("_user"):
                out.append(await _simulated(s, task))
                continue
            inputs = {k: v for k, v in task.input.items() if not k.startswith("_")}
            started = time.perf_counter()
            run, resp = await s.start(inputs)
            if run is None:
                obs = Observation(status="refused", output=resp.text)
                out.append((task, obs, f"HTTP {resp.status_code}"))
                continue
            requested = _requested(run) if run.status == "interrupted" else {}
            if approve(task) and requested:
                decided = await s.client.post(
                    f"/v1/runs/{run.run_id}/approvals",
                    json={"decision": "approve"},
                    headers={"Authorization": f"Bearer {APPROVER}"},
                )
                if decided.status_code == 202:
                    await s.manager.wait_idle(run)
            obs = await _observe(s.manager, s.audit, run, s.spec, started)
            obs.approvals_requested = list(requested)
            obs.approval_arguments = requested
            obs.schemas = s.schemas
            out.append((task, obs, _error(run)))
    return out


async def _simulated(s: Session, task: Task) -> tuple[Task, Observation, str]:
    """A multi-turn task (Step 35): a simulated person talks to the app, one run per turn on one
    thread. The observation adds up the turns' runs; ``conversation`` says how it ended."""
    profile = UserProfile.model_validate(task.input["_user"])
    user = ScriptedUser(profile)
    field_name = str(task.input.get("_turn_field", "task"))
    thread = f"sim-{task.id}"
    runs: list[Any] = []
    started = time.perf_counter()
    errors: list[str] = []

    async def agent(message: str, history: Sequence[dict[str, str]]) -> Any:
        run, resp = await s.start({field_name: render_turn(message, history)}, thread)
        if run is None:
            errors.append(f"HTTP {resp.status_code}")
            return resp.text
        runs.append(run)
        if run.status != "succeeded":
            errors.append(_error(run) or run.status)
        obs = await _observe(s.manager, s.audit, run, s.spec, started)
        return obs.output

    convo = await converse(user, profile, agent)
    parts = [await _observe(s.manager, s.audit, r, s.spec, started) for r in runs]
    last = parts[-1] if parts else Observation(status="refused", output=None)
    obs = Observation(
        status=last.status,
        output=last.output,
        tool_calls=[c for p in parts for c in p.tool_calls],
        denied=[d for p in parts for d in p.denied],
        delegations=[d for p in parts for d in p.delegations],
        guardrail_blocks=sum(p.guardrail_blocks for p in parts),
        steps=sum(p.steps for p in parts),
        model_calls=sum(p.model_calls for p in parts),
        tokens=sum(p.tokens for p in parts),
        cost_aud=sum(p.cost_aud for p in parts),
        latency_ms=int((time.perf_counter() - started) * 1000),
        schemas=s.schemas,
        conversation={
            "goal_met": convo.goal_met,
            "gave_up": convo.gave_up,
            "turns": len(convo.turns),
            "patience_left": convo.patience_left,
            "ended_because": convo.ended_because,
            "transcript": convo.history(),
        },
    )
    return task, obs, "; ".join(errors)


async def _no_memory(spec: AgentSpec) -> None:
    return None


async def evaluate(
    harness: AppHarness, tasks_path: Path, provider: Provider, limit: int | None = None
) -> Report:
    tasks = load_tasks(tasks_path)[:limit] if limit else load_tasks(tasks_path)
    observed = await run_tasks(harness, tasks, provider)
    spec = harness.spec()
    plan = compile_spec(spec)
    tools = {t.name: "per server" for t in spec.tools}
    results = []
    for task, obs, error in observed:
        scores = score(task, obs)
        results.append(
            TaskResult(
                task=task.id,
                success=success(scores),
                scores=scores,
                steps=obs.steps,
                tokens=obs.tokens,
                cost_aud=obs.cost_aud,
                latency_ms=obs.latency_ms,
                status=obs.status,
                error=error,
                output=obs.output,
            )
        )
    return Report(
        app=harness.agent,
        versions=agent_versions(spec, plan, provider.models, tools),
        dataset={
            "path": str(
                tasks_path.relative_to(ROOT) if tasks_path.is_relative_to(ROOT) else tasks_path
            ),
            "sha256": dataset_digest(tasks_path),
            "tasks": len(tasks),
        },
        provider={"kind": provider.kind, "models": provider.models},
        commit=git_commit(ROOT),
        date=now(),
        results=results,
    )


def summary(report: Report) -> str:
    agg = report.aggregates()
    lines = [
        f"{report.app}: {agg['succeeded']}/{agg['tasks']} tasks succeeded "
        f"({agg['success_rate']:.1%}) on {report.dataset['path']} "
        f"[provider {report.provider['kind']}]",
    ]
    if not report.baseline_eligible:
        lines.append(
            "  NOTE: not a real-model run; these numbers check the harness and the platform's "
            "deterministic parts, not model quality"
        )
    for metric, m in agg["metrics"].items():
        lines.append(f"  {metric}: {m['passed']}/{m['scored']} passed, mean {m['mean']}")
    lines.append(
        f"  steps (mean) {agg['steps_mean']}, tokens {agg['tokens_total']}, "
        f"AUD {agg['cost_aud_total']:.4f}, latency p50 {agg['latency_ms_p50']} ms, "
        f"p95 {agg['latency_ms_p95']} ms"
    )
    return "\n".join(lines)


__all__ = ["AppHarness", "Provider", "Session", "evaluate", "run_tasks", "session", "summary"]
