"""Runs behind the API: who owns them, their AG-UI event log, and their lifecycle.

* **Ownership.** A run belongs to the user (and tenant) who created it. Others get "not found";
  a person in the approver group of the run's pending approval, in the same tenant, may also see
  it and decide.
* **Idempotent creation.** ``Idempotency-Key`` plus the same body returns the same run; the same
  key with a different body is refused.
* **Event log.** Every AG-UI event is kept in order with its index, so a subscriber can join late
  or reconnect with ``after=<last index>`` and miss nothing.
* **Approvals.** The decision is made from the approver's verified token: their username and
  groups, never fields the client sends. The requester cannot approve their own run. Approval
  interrupts cannot be answered through the generic resume.
* State is in memory until durable checkpoints arrive (Step 18).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
from collections.abc import AsyncIterator, Coroutine, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_compiler.compiler import compile_spec, runtime_for
from agent_contracts.interfaces import RuntimeAdapter
from agent_contracts.models import (
    ExecutablePlan,
    RunContext,
    RunEvent,
    RunEventType,
    RunResult,
    RunState,
)
from agent_runtime.approvals import in_group
from agent_runtime.events import current_sink, json_safe
from agent_runtime.executors import resolve
from agent_spec import AgentSpec, load_spec
from agent_tools.authorising import Caller, current_caller
from agent_tools.identity import subject_token

from agent_api.agui import Translator
from agent_api.assembly import Assembly


@dataclass(frozen=True)
class Principal:
    user: str
    tenant: str
    groups: tuple[str, ...]
    roles: tuple[str, ...]
    token: str
    client_id: str = ""

    @property
    def subject(self) -> dict[str, Any]:
        return {
            "authenticated": True,
            "user": self.user,
            "client_id": self.client_id,
            "groups": list(self.groups),
            "roles": list(self.roles),
        }


@dataclass
class Run:
    run_id: str
    thread_id: str
    agent: str
    owner: Principal
    ctx: RunContext
    plan: ExecutablePlan
    adapter: RuntimeAdapter
    handle: str
    inputs: dict[str, Any]
    status: str = "pending"
    active: bool = False
    interrupts: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task[None] | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    translator: Translator | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "agent": self.agent,
            "status": self.status,
            "created_at": self.created_at,
            "interrupts": self.interrupts,
            "events": len(self.events),
        }


class ConflictError(ValueError):
    pass


class RunManager:
    def __init__(self, agents: Mapping[str, AgentSpec], assembly: Assembly) -> None:
        self.agents = dict(agents)
        self.assembly = assembly
        self.runs: dict[str, Run] = {}
        self.idempotency: dict[tuple[str, str, str], tuple[str, str]] = {}

    @classmethod
    def load_agents(cls, directory: str | Path) -> dict[str, AgentSpec]:
        agents = {}
        for path in sorted(Path(directory).glob("*/agent.yaml")):
            spec = load_spec(path)
            agents[spec.name] = spec
        return agents

    # ------------------------------------------------------------ access

    def _visible(self, run: Run, who: Principal) -> bool:
        if (who.tenant, who.user) == (run.owner.tenant, run.owner.user):
            return True
        return who.tenant == run.owner.tenant and any(
            in_group(who.groups, i.get("approvers")) for i in run.interrupts if i.get("approvers")
        )

    def get(self, run_id: str, who: Principal) -> Run:
        run = self.runs.get(run_id)
        if run is None or not self._visible(run, who):
            raise KeyError(run_id)
        return run

    def threads(self, who: Principal) -> list[dict[str, Any]]:
        mine = [
            r
            for r in self.runs.values()
            if (r.owner.tenant, r.owner.user) == (who.tenant, who.user)
        ]
        threads: dict[str, list[dict[str, Any]]] = {}
        for r in sorted(mine, key=lambda r: r.created_at):
            threads.setdefault(r.thread_id, []).append(r.summary())
        return [{"thread_id": t, "runs": runs} for t, runs in threads.items()]

    # ------------------------------------------------------------ lifecycle

    async def create(
        self,
        agent: str,
        inputs: dict[str, Any],
        who: Principal,
        idempotency_key: str | None = None,
        thread_id: str | None = None,
    ) -> tuple[Run, bool]:
        if agent not in self.agents:
            raise KeyError(agent)
        body = hashlib.sha256(
            json.dumps([agent, inputs, thread_id], sort_keys=True, default=str).encode()
        ).hexdigest()
        if idempotency_key:
            known = self.idempotency.get((who.tenant, who.user, idempotency_key))
            if known is not None:
                if known[1] != body:
                    raise ConflictError("this Idempotency-Key was used with a different request")
                return self.runs[known[0]], False
        spec = self.agents[agent]
        plan = compile_spec(spec)
        parts = await self.assembly.build(spec, plan)
        adapter = runtime_for(spec, runner=parts.runner)
        handle = await adapter.build(plan)
        ctx = RunContext(
            tenant_id=who.tenant,
            user_id=who.user,
            agent_id=spec.name,
            risk_tier=spec.risk_tier,
            **({"thread_id": thread_id} if thread_id else {}),
        )
        run = Run(
            run_id=ctx.run_id,
            thread_id=ctx.thread_id,
            agent=agent,
            owner=who,
            ctx=ctx,
            plan=plan,
            adapter=adapter,
            handle=handle,
            inputs=inputs,
        )
        run.translator = Translator(run.thread_id)
        self.runs[run.run_id] = run
        if idempotency_key:
            self.idempotency[(who.tenant, who.user, idempotency_key)] = (run.run_id, body)
        self._start(run, adapter.invoke(handle, inputs, ctx), first=True)
        return run, True

    async def approve(
        self, run: Run, who: Principal, approved: bool, comment: str | None = None
    ) -> None:
        if run.active or run.status != "interrupted":
            raise ConflictError(f"run {run.run_id} is {run.status}, not waiting for an approval")
        if who.tenant != run.owner.tenant:
            raise PermissionError("approvers must belong to the run's tenant")
        if who.user == run.owner.user:
            raise PermissionError("the requester cannot approve their own run")
        required = [i.get("approvers") for i in run.interrupts if i.get("reason") == "approval"]
        if not all(in_group(who.groups, r) for r in required if r):
            raise PermissionError(f"approver is not in {', '.join(str(r) for r in required)}")
        decision = {
            "approved": approved,
            "by": who.user,
            "groups": list(who.groups),
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "comment": comment or "",
        }
        self._start(run, run.adapter.resume(run.handle, run.run_id, decision, run.ctx))

    async def resume(self, run: Run, who: Principal, payload: dict[str, Any]) -> None:
        if (who.tenant, who.user) != (run.owner.tenant, run.owner.user):
            raise PermissionError("only the requester may resume a run")
        if run.active or run.status != "interrupted":
            raise ConflictError(f"run {run.run_id} is {run.status}, not interrupted")
        if any(i.get("reason") == "approval" for i in run.interrupts):
            raise ConflictError("this run waits for an approval: use /approvals")
        self._start(run, run.adapter.resume(run.handle, run.run_id, payload, run.ctx))

    async def cancel(self, run: Run, who: Principal) -> None:
        if (who.tenant, who.user) != (run.owner.tenant, run.owner.user):
            raise PermissionError("only the requester may cancel a run")
        if run.task is not None and not run.task.done():
            run.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run.task
        elif run.status == "interrupted":
            with contextlib.suppress(KeyError):
                await run.adapter.cancel(run.run_id)
            await self._finish_cancelled(run)

    # ------------------------------------------------------------ execution

    def _start(self, run: Run, work: Coroutine[Any, Any, RunResult], first: bool = False) -> None:
        run.active, run.status, run.interrupts = True, "running", []
        run.task = asyncio.create_task(self._execute(run, work, first))

    async def _record(self, run: Run, event: RunEvent) -> None:
        assert run.translator is not None
        async with run.changed:
            for e in run.translator.translate(event):
                run.events.append(e.model_dump(mode="json", by_alias=True, exclude_none=True))
            run.changed.notify_all()

    async def _execute(self, run: Run, work: Coroutine[Any, Any, RunResult], first: bool) -> None:
        sink_token = current_sink.set(lambda e: self._record(run, e))
        user_token = subject_token.set(run.owner.token)
        caller_token = current_caller.set(
            Caller(subject=run.owner.subject, via="agent-api", client_id=run.owner.client_id)
        )
        try:
            started = {"input": json_safe(run.inputs)} if first else {}
            await self._record(
                run, RunEvent(type=RunEventType.RUN_STARTED, run_id=run.run_id, data=started)
            )
            result = await work
            run.status = result.status.value
            if run.status == "failed":
                await self._record(
                    run,
                    RunEvent(
                        type=RunEventType.RUN_ERROR,
                        run_id=run.run_id,
                        data={"message": result.state.error or "run failed", "code": "failed"},
                    ),
                )
            else:
                run.interrupts = (
                    self._interrupts(run, result.state) if run.status == "interrupted" else []
                )
                await self._record(
                    run,
                    RunEvent(
                        type=RunEventType.RUN_FINISHED,
                        run_id=run.run_id,
                        data=json_safe(
                            {
                                "status": run.status,
                                "output": result.output if run.status == "succeeded" else None,
                                "stop_reason": result.state.stop_reason,
                                "interrupts": run.interrupts,
                            }
                        ),
                    ),
                )
        except asyncio.CancelledError:
            await self._finish_cancelled(run)
        except Exception as exc:  # the run fails; the API keeps serving
            run.status = "failed"
            await self._record(
                run,
                RunEvent(
                    type=RunEventType.RUN_ERROR,
                    run_id=run.run_id,
                    data={"message": f"{type(exc).__name__}: {exc}", "code": "error"},
                ),
            )
        finally:
            current_caller.reset(caller_token)
            subject_token.reset(user_token)
            current_sink.reset(sink_token)
            async with run.changed:
                run.active = False
                run.changed.notify_all()

    async def _finish_cancelled(self, run: Run) -> None:
        run.status, run.interrupts = "cancelled", []
        await self._record(
            run,
            RunEvent(
                type=RunEventType.RUN_FINISHED, run_id=run.run_id, data={"status": "cancelled"}
            ),
        )

    def _interrupts(self, run: Run, state: RunState) -> list[dict[str, Any]]:
        out = []
        for node_id in state.next_nodes:
            cfg = run.plan.node(node_id).config
            item: dict[str, Any] = {
                "id": f"{run.run_id}:{node_id}",
                "reason": "approval",
                "node": node_id,
                "approvers": cfg.get("approvers"),
            }
            if cfg.get("tool"):
                target = run.plan.node(
                    next(e.target for e in run.plan.edges if e.source == node_id)
                )
                try:
                    args = resolve(
                        (target.config.get("config") or {}).get("args", {}), state.values
                    )
                except KeyError:
                    args = None
                item |= {
                    "message": f"Approve {cfg['tool']}?",
                    "action": {"tool": cfg["tool"], "arguments": args},
                }
            elif cfg.get("agent"):
                loop = run.plan.node(str(cfg["agent"])).config
                pending = (state.values.get(str(cfg["agent"])) or {}).get("pending", [])
                approvers = dict(loop.get("approvers") or {})
                item |= {
                    "message": "Approve the agent's proposed action?",
                    "action": {
                        "calls": [
                            {"tool": c["name"], "arguments": c.get("arguments")} for c in pending
                        ]
                    },
                    "approvers": next(iter(approvers.values()), None),
                }
            out.append(item)
        return out

    async def events(self, run: Run, after: int = -1) -> AsyncIterator[tuple[int, dict[str, Any]]]:
        """Events with index > ``after``; ends when the run is idle (paused or finished) and every
        event has been delivered."""
        cursor = after + 1
        while True:
            async with run.changed:
                while cursor >= len(run.events) and run.active:
                    await run.changed.wait()
                batch = run.events[cursor:]
                idle = not run.active
            for event in batch:
                yield cursor, event
                cursor += 1
            if idle and cursor >= len(run.events):
                return

    async def wait_idle(self, run: Run) -> None:
        async with run.changed:
            while run.active:
                await run.changed.wait()
