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
* **Durability (Step 18).** Runs and their event logs are saved in a run store; with a
  checkpointer every node is checkpointed, so after a restart running runs continue from their
  checkpoints and paused runs wait for their approval again (``recover``). Threads can be
  inspected and forked from an earlier checkpoint.
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
from agent_contracts.dummies.safety import InMemoryApprovalGate
from agent_contracts.interfaces import RuntimeAdapter
from agent_contracts.models import (
    AuditEvent,
    ExecutablePlan,
    MemoryScope,
    RunContext,
    RunEvent,
    RunEventType,
    RunResult,
    RunState,
    ToolSpec,
)
from agent_runtime.approvals import in_group
from agent_runtime.checkpoints import fork_thread, history
from agent_runtime.durable import load_journal
from agent_runtime.events import current_sink, json_safe
from agent_runtime.executors import resolve
from agent_runtime.nodes import RunJournal
from agent_spec import AgentSpec, load_spec
from agent_tools.approvals import RulesApprovalRouter
from agent_tools.assemble import tool_names
from agent_tools.authorising import Caller, current_caller
from agent_tools.identity import subject_token

from agent_api.agui import Translator
from agent_api.assembly import Assembly
from agent_api.desk import ApprovalDesk, ApprovalExpiredError
from agent_api.store import MemoryRunStore, RunStore


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
    adapter: RuntimeAdapter | None  # None for finished runs reloaded after a restart
    handle: str
    inputs: dict[str, Any]
    status: str = "pending"
    idempotency_key: str | None = None
    body_hash: str | None = None
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

    def record(self) -> dict[str, Any]:
        """What the run store keeps (never the access token)."""
        return {
            "run_id": self.run_id,
            "tenant": self.owner.tenant,
            "user": self.owner.user,
            "groups": list(self.owner.groups),
            "roles": list(self.owner.roles),
            "client_id": self.owner.client_id,
            "thread_id": self.thread_id,
            "agent": self.agent,
            "inputs": self.inputs,
            "status": self.status,
            "interrupts": self.interrupts,
            "created_at": self.created_at,
            "idempotency_key": self.idempotency_key,
            "body_hash": self.body_hash,
        }

    def live(self) -> RuntimeAdapter:
        if self.adapter is None:
            raise ConflictError(f"run {self.run_id} is {self.status} and cannot continue")
        return self.adapter


#: where each loaded agent's spec file lives: team workers are resolved relative to it
SPEC_PATHS: dict[str, Path] = {}


class ConflictError(ValueError):
    pass


class RunManager:
    def __init__(
        self,
        agents: Mapping[str, AgentSpec],
        assembly: Assembly,
        store: RunStore | None = None,
        desk: ApprovalDesk | None = None,
    ) -> None:
        self.agents = dict(agents)
        self.assembly = assembly
        self.store = store or MemoryRunStore()
        self.runs: dict[str, Run] = {}
        self.idempotency: dict[tuple[str, str, str], tuple[str, str]] = {}
        # Step 28: routed, timed, audited approvals (in memory unless given a durable gate)
        self.desk = desk or ApprovalDesk(
            InMemoryApprovalGate(), RulesApprovalRouter(), assembly.audit
        )
        self.tool_specs: dict[str, dict[str, ToolSpec]] = {}  # agent -> tool name -> spec

    @classmethod
    def load_agents(cls, directory: str | Path) -> dict[str, AgentSpec]:
        agents = {}
        for path in sorted(Path(directory).glob("*/agent.yaml")):
            spec = load_spec(path)
            agents[spec.name] = spec
            SPEC_PATHS[spec.name] = path.resolve()
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
        ctx = RunContext(
            tenant_id=who.tenant,
            user_id=who.user,
            agent_id=spec.name,
            agent_version=spec.version,
            risk_tier=spec.risk_tier,
            **({"thread_id": thread_id} if thread_id else {}),
        )
        if self.assembly.admit is not None:  # Step 25: only approved agents in staging and prod
            refused = await self.assembly.admit(spec, ctx)
            if refused:
                raise PermissionError(refused)
        plan = compile_spec(spec)
        adapter, handle = await self._prepare(spec, plan, None)
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
            idempotency_key=idempotency_key,
            body_hash=body,
        )
        run.translator = Translator(run.thread_id)
        self.runs[run.run_id] = run
        if idempotency_key:
            self.idempotency[(who.tenant, who.user, idempotency_key)] = (run.run_id, body)
        await self.store.save(run.record())
        self._start(run, adapter.invoke(handle, inputs, ctx), first=True)
        return run, True

    async def _prepare(
        self, spec: AgentSpec, plan: ExecutablePlan, journal: RunJournal | None
    ) -> tuple[RuntimeAdapter, str]:
        parts = await self.assembly.build(spec, plan, journal, spec_path=SPEC_PATHS.get(spec.name))
        registry = getattr(parts.tools, "registry", None)
        if registry is not None:  # side effects of the agent's tools, for approval routing
            specs = {t.name: t for t in await registry.list()}
            names = tool_names(plan)
            self.tool_specs[spec.name] = specs | {
                k: specs[v] for k, v in names.items() if v in specs
            }
        adapter = runtime_for(spec, runner=parts.runner)
        return adapter, await adapter.build(plan)

    # ------------------------------------------------------------ durability (Step 18)

    async def recover(self) -> list[str]:
        """After a restart: reload every stored run, continue the ones that were running from
        their checkpoints and bring the ones waiting for an approval back to that point."""
        actions = []
        for rec in await self.store.records():
            who = Principal(
                user=rec["user"],
                tenant=rec["tenant"],
                groups=tuple(rec.get("groups", ())),
                roles=tuple(rec.get("roles", ())),
                token="",  # tokens are never stored; on-behalf-of tools need a new one
                client_id=rec.get("client_id", ""),
            )
            spec = self.agents.get(rec["agent"])
            status = str(rec["status"])
            active = status in ("pending", "running", "interrupted")
            plan = compile_spec(spec) if spec else None
            ctx = RunContext(
                tenant_id=who.tenant,
                user_id=who.user,
                agent_id=rec["agent"],
                run_id=rec["run_id"],
                thread_id=rec["thread_id"],
                **({"risk_tier": spec.risk_tier} if spec else {}),
            )
            journal = None
            checkpointer = self.assembly.checkpointer
            if active and checkpointer is not None:
                journal = await load_journal(checkpointer, ctx)
            adapter, handle = (None, "")
            if active and spec and plan and checkpointer is not None:
                adapter, handle = await self._prepare(spec, plan, journal)
            run = Run(
                run_id=rec["run_id"],
                thread_id=rec["thread_id"],
                agent=rec["agent"],
                owner=who,
                ctx=ctx,
                plan=plan
                or ExecutablePlan.model_construct(name=rec["agent"], nodes=(), entry="", exits=()),
                adapter=adapter,
                handle=handle,
                inputs=dict(rec.get("inputs") or {}),
                status=status,
                interrupts=list(rec.get("interrupts") or []),
                events=await self.store.events(rec["run_id"]),
                created_at=str(rec.get("created_at")),
                idempotency_key=rec.get("idempotency_key"),
                body_hash=rec.get("body_hash"),
            )
            run.translator = Translator(run.thread_id)
            run.translator.started = True
            self.runs[run.run_id] = run
            if run.idempotency_key and run.body_hash:
                self.idempotency[(who.tenant, who.user, run.idempotency_key)] = (
                    run.run_id,
                    run.body_hash,
                )
            if not active:
                continue
            if adapter is None:
                await self._fail(run, "the service restarted and this run has no checkpoints")
                actions.append(f"{run.run_id}: failed (no checkpoints)")
            elif status == "interrupted":
                await self._restore_interrupt(run)
                actions.append(f"{run.run_id}: waiting for approval again")
            else:
                self._start(run, adapter.invoke(handle, run.inputs, ctx))
                actions.append(f"{run.run_id}: resumed from checkpoints")
        return actions

    async def _restore_interrupt(self, run: Run) -> None:
        """Replay a paused run to its approval point without emitting anything."""
        caller = current_caller.set(
            Caller(subject=run.owner.subject, via="agent-api", client_id=run.owner.client_id)
        )
        try:
            result = await run.live().invoke(run.handle, run.inputs, run.ctx)
        finally:
            current_caller.reset(caller)
        if result.status.value != "interrupted":
            await self._fail(run, f"after a restart the run replayed to {result.status.value}")

    async def _fail(self, run: Run, message: str) -> None:
        run.status, run.interrupts = "failed", []
        await self._record(
            run, RunEvent(type=RunEventType.RUN_ERROR, run_id=run.run_id, data={"message": message})
        )
        await self.store.save(run.record())

    async def checkpoints(self, thread_id: str, who: Principal) -> list[dict[str, Any]]:
        """Time travel, inspect: the checkpoints of one of the caller's threads."""
        self._thread_runs(thread_id, who)
        if self.assembly.checkpointer is None:
            return []
        return await history(self.assembly.checkpointer, who.tenant, thread_id)

    def _thread_runs(self, thread_id: str, who: Principal) -> list[Run]:
        runs = [
            r
            for r in self.runs.values()
            if r.thread_id == thread_id and (r.owner.tenant, r.owner.user) == (who.tenant, who.user)
        ]
        if not runs:
            raise KeyError(thread_id)
        return runs

    async def fork(self, thread_id: str, checkpoint_id: str, who: Principal) -> Run:
        """Time travel, fork: a new thread with the history up to ``checkpoint_id``; a new run
        continues from there (completed steps are not repeated)."""
        checkpointer = self.assembly.checkpointer
        if checkpointer is None:
            raise ConflictError("forking needs a checkpointer")
        point = await checkpointer.get(who.tenant, thread_id, checkpoint_id)
        if point is None:
            raise KeyError(checkpoint_id)
        source = next(
            (r for r in self._thread_runs(thread_id, who) if r.run_id == point.run_id), None
        )
        if source is None:
            raise KeyError(checkpoint_id)
        new_thread, new_run = await fork_thread(checkpointer, who.tenant, thread_id, checkpoint_id)
        spec = self.agents[source.agent]
        ctx = RunContext(
            tenant_id=who.tenant,
            user_id=who.user,
            agent_id=spec.name,
            risk_tier=spec.risk_tier,
            run_id=new_run,
            thread_id=new_thread,
        )
        journal = await load_journal(checkpointer, ctx)
        adapter, handle = await self._prepare(spec, source.plan, journal)
        run = Run(
            run_id=new_run,
            thread_id=new_thread,
            agent=source.agent,
            owner=who,
            ctx=ctx,
            plan=source.plan,
            adapter=adapter,
            handle=handle,
            inputs=dict(source.inputs),
        )
        run.translator = Translator(new_thread)
        self.runs[new_run] = run
        await self.store.save(run.record())
        self._start(run, adapter.invoke(handle, run.inputs, ctx), first=True)
        return run

    async def approve(
        self, run: Run, who: Principal, approved: bool, comment: str | None = None
    ) -> str:
        return await self.decide(run, who, "approve" if approved else "reject", None, comment)

    async def decide(
        self,
        run: Run,
        who: Principal,
        action: str,
        arguments: dict[str, Any] | None = None,
        comment: str | None = None,
    ) -> str:
        """A reviewer's decision (Step 28): ``approve`` (optionally with edited arguments),
        ``reject`` or ``escalate``. Returns ``resumed`` or what the approval now waits for."""
        if run.active or run.status != "interrupted":
            raise ConflictError(f"run {run.run_id} is {run.status}, not waiting for an approval")
        item = next((i for i in run.interrupts if i.get("reason") == "approval"), None)
        if item is None:
            raise ConflictError("this run is not waiting for an approval")
        if not item.get("approval_id"):  # paused before it was filed (e.g. recovered): file now
            await self.desk.open(
                [item],
                run.ctx,
                self.agents[run.agent].risk_tier,
                self.tool_specs.get(run.agent, {}),
            )
        try:
            verdict = await self.desk.decide(item, run.ctx, who, action, arguments, comment or "")
        except ApprovalExpiredError:
            await self.expire(run)
            raise ConflictError(
                "the approval expired: the run was cancelled and nothing ran"
            ) from None
        if verdict.resume is None:
            await self.store.save(run.record())
            return str(verdict.waiting)
        self._start(run, run.live().resume(run.handle, run.run_id, verdict.resume, run.ctx))
        return "resumed"

    def inbox(self, who: Principal) -> list[dict[str, Any]]:
        """Approvals this person may decide: same tenant, in the approver group, not their own."""
        out = []
        for run in self.runs.values():
            if (
                run.status != "interrupted"
                or run.owner.tenant != who.tenant
                or run.owner.user == who.user
            ):
                continue
            for item in run.interrupts:
                if item.get("reason") == "approval" and in_group(who.groups, item.get("approvers")):
                    out.append(
                        {
                            "run_id": run.run_id,
                            "agent": run.agent,
                            "requested_by": run.owner.user,
                            "proposed": item.get("action"),
                            "reason": item.get("message"),
                            "risk": item.get("risk"),
                            "side_effect": item.get("side_effect"),
                            "approvers": item.get("approvers"),
                            "two_person": item.get("two_person"),
                            "approvals_so_far": [a["by"] for a in item.get("approvals", [])],
                            "expires_at": item.get("expires_at"),
                            "escalated": item.get("escalated"),
                            "routing": item.get("routing"),
                            "input": run.inputs,
                        }
                    )
        return sorted(out, key=lambda x: str(x["expires_at"]))

    async def sweep_approvals(self) -> list[tuple[str, str]]:
        """SLA timers: overdue approvals escalate once, then expire (the run is cancelled)."""
        outcomes = []
        for run in list(self.runs.values()):
            if run.status != "interrupted" or run.active:
                continue
            for item in [i for i in run.interrupts if i.get("reason") == "approval"]:
                if self.desk.is_overdue(item):
                    outcome = await self.desk.overdue(item, run.ctx)
                    outcomes.append((run.run_id, outcome))
                    if outcome == "expired":
                        await self.expire(run)
                    else:
                        await self.store.save(run.record())
                    break
        return outcomes

    async def expire(self, run: Run) -> None:
        """Cancel a run whose approval expired. Nothing behind the approval has run."""
        with contextlib.suppress(KeyError, ConflictError):
            await run.live().cancel(run.run_id)
        await self._finish_cancelled(run, reason="approval expired")

    async def resume(self, run: Run, who: Principal, payload: dict[str, Any]) -> None:
        if (who.tenant, who.user) != (run.owner.tenant, run.owner.user):
            raise PermissionError("only the requester may resume a run")
        if run.active or run.status != "interrupted":
            raise ConflictError(f"run {run.run_id} is {run.status}, not interrupted")
        if any(i.get("reason") == "approval" for i in run.interrupts):
            raise ConflictError("this run waits for an approval: use /approvals")
        self._start(run, run.live().resume(run.handle, run.run_id, payload, run.ctx))

    async def cancel(self, run: Run, who: Principal) -> None:
        if (who.tenant, who.user) != (run.owner.tenant, run.owner.user):
            raise PermissionError("only the requester may cancel a run")
        if run.task is not None and not run.task.done():
            run.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run.task
        elif run.status == "interrupted":
            with contextlib.suppress(KeyError, ConflictError):
                await run.live().cancel(run.run_id)
            await self._finish_cancelled(run)

    # ------------------------------------------------------------ right to erasure

    PRIVACY_ROLE = "privacy-officer"

    async def erase_subject(self, tenant: str, user: str, who: Principal) -> dict[str, int]:
        """Remove everything the platform holds about one person in one tenant: their runs
        (stopped first), event logs, checkpoint threads, and every agent's long-term memory
        about them. Only a privacy officer of that tenant may ask. The erasure itself is
        audited (who asked, counts), since the audit log must show it happened."""
        if self.PRIVACY_ROLE not in who.roles or who.tenant != tenant:
            raise PermissionError("erasure needs the privacy-officer role in the same tenant")
        theirs = [r for r in self.runs.values() if (r.owner.tenant, r.owner.user) == (tenant, user)]
        for run in theirs:
            if run.task is not None and not run.task.done():
                run.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await run.task
        threads = {r.thread_id for r in theirs}
        if self.assembly.checkpointer is not None:
            for thread in threads:
                await self.assembly.checkpointer.delete_thread(tenant, thread)
        runs = await self.store.erase(tenant, user)
        memories = 0
        for spec in self.agents.values():
            memory = await self.assembly.memory_factory(spec)
            if memory is not None:
                memories += await memory.erase_subject(tenant, user)
        for run in theirs:
            self.runs.pop(run.run_id, None)
        self.idempotency = {
            k: v for k, v in self.idempotency.items() if (k[0], k[1]) != (tenant, user)
        }
        counts = {"runs": runs, "threads": len(threads), "memories": memories}
        await self.assembly.audit.append(
            AuditEvent(
                type="subject.erased",
                ctx=RunContext(tenant_id=tenant, user_id=who.user, agent_id="agent-api"),
                payload={"subject": user, "requested_by": who.user, **counts},
            )
        )
        return counts

    # ------------------------------------------------------------ inspect and flush memory

    INCIDENT_ROLES = frozenset({"privacy-officer", "security-officer"})

    async def _memory(self, agent: str) -> Any:
        spec = self.agents.get(agent)
        memory = await self.assembly.memory_factory(spec) if spec else None
        if memory is None:
            raise KeyError(agent)
        return memory

    async def memories(self, agent: str, who: Principal) -> list[dict[str, Any]]:
        """Inspect: what this agent remembers that the caller can see (theirs and shared)."""
        memory = await self._memory(agent)
        scope = MemoryScope(tenant_id=who.tenant, user_id=who.user, agent_id=agent)
        return [
            {
                "id": i.id,
                "kind": i.kind.value,
                "key": i.key,
                "version": i.version,
                "content": i.content,
                "shared": i.scope.user_id is None,
                "source": i.provenance.source,
                "trust": i.provenance.trust.value,
                "run_id": i.provenance.run_id,
                "created_at": i.created_at.isoformat(),
                "expires_at": i.expires_at.isoformat() if i.expires_at else None,
            }
            for i in await memory.store.items(scope)
        ]

    async def forget(self, agent: str, item_id: str, who: Principal) -> None:
        """A user deletes one of their own memories (shared ones need an officer's flush)."""
        memory = await self._memory(agent)
        scope = MemoryScope(tenant_id=who.tenant, user_id=who.user, agent_id=agent)
        item = await memory.store.get(scope, item_id)
        if item is None:
            raise KeyError(item_id)
        if item.scope.user_id != who.user:
            raise PermissionError("only your own memories can be deleted here")
        await memory.store.delete(scope, item_id)

    async def flush_run(self, agent: str, run_id: str, who: Principal) -> int:
        """Incident response: remove every memory one run wrote (e.g. a run later found to
        have been steered by a poisoned document). Officers only; audited."""
        if not self.INCIDENT_ROLES & set(who.roles):
            raise PermissionError("flushing memory needs the privacy- or security-officer role")
        memory = await self._memory(agent)
        flushed = int(await memory.store.flush_run(who.tenant, run_id))
        await self.assembly.audit.append(
            AuditEvent(
                type="memory.flushed",
                ctx=RunContext(tenant_id=who.tenant, user_id=who.user, agent_id=agent),
                payload={"run_id": run_id, "flushed": flushed, "requested_by": who.user},
            )
        )
        return flushed

    # ------------------------------------------------------------ execution

    def _start(self, run: Run, work: Coroutine[Any, Any, RunResult], first: bool = False) -> None:
        run.active, run.status, run.interrupts = True, "running", []
        run.task = asyncio.create_task(self._execute(run, work, first))

    async def _record(self, run: Run, event: RunEvent) -> None:
        assert run.translator is not None
        async with run.changed:
            for e in run.translator.translate(event):
                dumped = e.model_dump(mode="json", by_alias=True, exclude_none=True)
                await self.store.append(run.run_id, len(run.events), dumped)
                run.events.append(dumped)
            run.changed.notify_all()

    async def _execute(self, run: Run, work: Coroutine[Any, Any, RunResult], first: bool) -> None:
        sink_token = current_sink.set(lambda e: self._record(run, e))
        user_token = subject_token.set(run.owner.token)
        caller_token = current_caller.set(
            Caller(subject=run.owner.subject, via="agent-api", client_id=run.owner.client_id)
        )
        try:
            await self.store.save(run.record())
            started = {"input": json_safe(run.inputs)} if first else {}
            await self._record(
                run, RunEvent(type=RunEventType.RUN_STARTED, run_id=run.run_id, data=started)
            )
            budgets = self.assembly.budgets
            spec = self.agents[run.agent]
            if not budgets.known(run.run_id):  # Step 26 (re-opened after a restart)
                await budgets.open(spec, run.ctx)
            result = await work
            if result.status.value != "interrupted":  # approvals keep the budget open
                summariser = (
                    self.assembly.model_factory(spec)
                    if spec.budgets.on_exceed == "summarise"
                    else None
                )
                result = await budgets.finish(spec, run.ctx, result, summariser)
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
                if run.interrupts:  # route, time and file the approvals (Step 28)
                    await self.desk.open(
                        run.interrupts,
                        run.ctx,
                        self.agents[run.agent].risk_tier,
                        self.tool_specs.get(run.agent, {}),
                    )
                await self._record(
                    run,
                    RunEvent(
                        type=RunEventType.RUN_FINISHED,
                        run_id=run.run_id,
                        data=json_safe(
                            {
                                "status": run.status,
                                # stopped runs keep their partial result and why (Step 26)
                                "output": result.output
                                if run.status in ("succeeded", "stopped")
                                else None,
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
            await self.store.save(run.record())
            async with run.changed:
                run.active = False
                run.changed.notify_all()

    async def _finish_cancelled(self, run: Run, reason: str | None = None) -> None:
        run.status, run.interrupts = "cancelled", []
        data: dict[str, Any] = {"status": "cancelled"}
        if reason:
            data["stop_reason"] = reason
        await self._record(
            run,
            RunEvent(type=RunEventType.RUN_FINISHED, run_id=run.run_id, data=data),
        )
        await self.store.save(run.record())

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
