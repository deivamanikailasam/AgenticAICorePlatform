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
* **Several replicas (Step 38).** Given its address (``node``), a manager shares the store with
  other replicas: it drives only the unfinished runs whose lease it holds and renews them; a
  request for a run held elsewhere is answered by that replica (``locate``), and a run whose
  holder stopped renewing is taken over and continued from its checkpoints. Lists (threads,
  approvals inbox) come from the store, so every replica gives the same answer.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import time
from collections.abc import AsyncIterator, Coroutine, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from agent_audit.redact import digest
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
    new_id,
)
from agent_runtime.approvals import in_group
from agent_runtime.checkpoints import fork_thread, history
from agent_runtime.durable import load_journal
from agent_runtime.events import current_sink, json_safe
from agent_runtime.executors import resolve
from agent_runtime.nodes import RunJournal
from agent_spec import AgentSpec, load_spec
from agent_telemetry.metrics import agent_metrics
from agent_telemetry.spans import agent_span, end_agent
from agent_tools.approvals import RulesApprovalRouter
from agent_tools.assemble import tool_names
from agent_tools.authorising import Caller, current_caller
from agent_tools.identity import subject_token

from agent_api.agui import Translator
from agent_api.assembly import Assembly
from agent_api.desk import ApprovalDesk, ApprovalExpiredError
from agent_api.releases import Outcome, Releases
from agent_api.store import IdempotencyRaceError, MemoryRunStore, RunStore

# runs that have ended: the only ones retention may prune
TERMINAL = ("succeeded", "failed", "stopped", "cancelled")


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


def plan_summary(plan: ExecutablePlan) -> dict[str, Any]:
    return {
        "plan_hash": str(plan.metadata.get("plan_hash")),
        "entry": plan.entry,
        "nodes": [
            {
                "id": n.id,
                "kind": n.kind.value,
                **({"tools": list(n.config["tools"])} if n.config.get("tools") else {}),
            }
            for n in plan.nodes
        ],
        "edges": [[e.source, e.target, e.condition] for e in plan.edges],
        "exits": list(plan.exits),
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
    # this replica lost the run's lease (Step 38): it stops driving it and writes nothing more
    detached: bool = False

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
            "agent_version": self.ctx.agent_version,
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
#: the same per (agent, version), for deployments that carry several versions (Step 38)
VERSION_PATHS: dict[tuple[str, str], Path] = {}


class ConflictError(ValueError):
    pass


class RunElsewhereError(Exception):
    """The run is driven by another replica; the request belongs there."""

    def __init__(self, peer: str) -> None:
        super().__init__(peer)
        self.peer = peer


class RunManager:
    def __init__(
        self,
        agents: Mapping[str, AgentSpec],
        assembly: Assembly,
        store: RunStore | None = None,
        desk: ApprovalDesk | None = None,
        node: str | None = None,
        releases: Releases | None = None,
    ) -> None:
        # Step 38: which version of each agent serves (stable, canary or shadow candidate)
        self.releases = releases or Releases.single(agents)
        self.agents = dict(agents) if releases is None else releases.stable()
        # Step 38: this replica's address when several share the store (None: the only one)
        self.node = node
        self.held: set[str] = set()
        self._shadows: set[asyncio.Task[None]] = set()
        self.assembly = assembly
        self.store = store or MemoryRunStore()
        self.runs: dict[str, Run] = {}
        self.idempotency: dict[tuple[str, str, str], tuple[str, str]] = {}
        # Step 28: routed, timed, audited approvals (in memory unless given a durable gate)
        self.desk = desk or ApprovalDesk(
            InMemoryApprovalGate(), RulesApprovalRouter(), assembly.audit
        )
        # Step 31: approval events get versions and trace ids like every other event
        if getattr(self.assembly.audit, "inner", None) is self.desk.audit:
            self.desk.audit = self.assembly.audit
        self.tool_specs: dict[str, dict[str, ToolSpec]] = {}  # agent -> tool name -> spec
        self.reviews: dict[str, dict[str, Any]] = {}  # run id -> review-after item (Step 28)
        agent_metrics().pending_from(self.pending_approvals)  # Step 30 gauge

    def pending_approvals(self) -> list[tuple[int, dict[str, Any]]]:
        """Approvals waiting for a decision, per agent (read by the metrics gauge)."""
        counts: dict[str, int] = {}
        for run in list(self.runs.values()):
            if run.status == "interrupted":
                waiting = sum(1 for i in run.interrupts if i.get("reason") == "approval")
                counts[run.agent] = counts.get(run.agent, 0) + waiting
        return [(n, {"gen_ai.agent.name": agent}) for agent, n in sorted(counts.items())]

    @classmethod
    def load_agents(cls, directory: str | Path) -> dict[str, AgentSpec]:
        agents = {}
        for path in sorted(Path(directory).glob("*/agent.yaml")):
            spec = load_spec(path)
            agents[spec.name] = spec
            SPEC_PATHS[spec.name] = path.resolve()
            VERSION_PATHS[(spec.name, spec.version)] = path.resolve()
        return agents

    @classmethod
    def load_versions(cls, directory: str | Path) -> dict[tuple[str, str], AgentSpec]:
        """Released specs, ``<agent>/<version>/agent.yaml`` (deploy/specs, Step 38). A version's
        directory must hold that version."""
        versions = {}
        for path in sorted(Path(directory).glob("*/*/agent.yaml")):
            spec = load_spec(path)
            if (spec.name, spec.version) != (path.parent.parent.name, path.parent.name):
                raise ValueError(f"{path} holds {spec.name} {spec.version}")
            versions[(spec.name, spec.version)] = spec
            VERSION_PATHS[(spec.name, spec.version)] = path.resolve()
        return versions

    def spec_of(self, run: Run) -> AgentSpec:
        """The spec version this run started on; it finishes on it (Step 38)."""
        found = self.releases.versions.get((run.agent, run.ctx.agent_version))
        return found if found is not None else self.agents[run.agent]

    # ------------------------------------------------------------ access

    def _visible(self, run: Run, who: Principal) -> bool:
        return self._may_see(run.record(), who)

    def _may_see(self, rec: Mapping[str, Any], who: Principal) -> bool:
        if (who.tenant, who.user) == (rec["tenant"], rec["user"]):
            return True
        if who.tenant == rec["tenant"] and self.TAKEOVER_ROLE in who.roles:
            return True  # operators may see and take over runs of their tenant (Step 28)
        return who.tenant == rec["tenant"] and any(
            in_group(who.groups, i.get("approvers"))
            for i in rec.get("interrupts") or []
            if i.get("approvers")
        )

    # ------------------------------------------------------------ replicas (Step 38)

    LEASE_SECONDS = 30.0

    async def _hold(self, run_id: str) -> bool:
        """Take (or keep) the run's lease; always true for a single replica."""
        if self.node is None:
            return True
        if await self.store.claim(run_id, self.node, self.LEASE_SECONDS) == self.node:
            self.held.add(run_id)
            return True
        return False

    async def locate(self, run_id: str) -> str | None:
        """Where a request for this run must be answered: None for here, else the address of
        the replica driving it. A finished run is read here; an unfinished one whose holder
        stopped renewing is taken over and continued here."""
        if self.node is None or run_id in self.held:
            return None
        local = self.runs.get(run_id)
        if local is not None and local.status in TERMINAL:
            return None
        rec = await self.store.record(run_id)
        if rec is None:
            return None
        if str(rec["status"]) in TERMINAL:
            self.runs.pop(run_id, None)
            await self._restore(rec)
            return None
        holder = await self.store.holder(run_id)
        if holder and holder != self.node:
            return holder
        if await self._hold(run_id):
            self.runs.pop(run_id, None)
            await self._restore(rec)
            return None
        return await self.store.holder(run_id)

    async def keep_leases(self) -> list[str]:
        """Renew this replica's leases; runs it no longer holds are let go without another write
        (their new holder continues them). Returns the runs let go."""
        if self.node is None:
            return []
        live = {r for r in self.held if r in self.runs and self.runs[r].status not in TERMINAL}
        kept = await self.store.renew(self.node, live, self.LEASE_SECONDS)
        lost = sorted(live - kept)
        self.held = kept
        for run_id in lost:
            run = self.runs.pop(run_id)
            run.detached = True
            if run.task is not None and not run.task.done():
                run.task.cancel()
        return lost

    async def adopt_orphans(self) -> list[str]:
        """Take over unfinished runs nobody holds (their replica stopped), so they continue
        without waiting for a request. Returns the runs taken over."""
        if self.node is None:
            return []
        taken = []
        for run_id in await self.store.orphans(("pending", "running", "interrupted")):
            if await self.locate(run_id) is None and run_id in self.held:
                taken.append(run_id)
        return taken

    PEER = re.compile(r"^[A-Za-z0-9.:\[\]-]+:[0-9]{1,5}$")

    def peer_client(self, peer: str) -> httpx.AsyncClient:
        """An HTTP client to another replica (``host:port`` from the lease table)."""
        if not self.PEER.match(peer):
            raise ConflictError("the run's replica address is malformed")
        return httpx.AsyncClient(base_url=f"http://{peer}", timeout=60.0)

    async def visible_record(self, run_id: str, who: Principal) -> dict[str, Any]:
        rec = await self.store.record(run_id)
        if rec is None or not self._may_see(rec, who):
            raise KeyError(run_id)
        return rec

    async def tail(
        self, run_id: str, who: Principal, after: int = -1, poll: float = 0.5
    ) -> AsyncIterator[tuple[int, dict[str, Any]]]:
        """A run's events read from the shared store, for a run another replica drives: ends
        like ``events`` once the run is paused or finished and everything is delivered."""
        await self.visible_record(run_id, who)
        cursor = after + 1
        while True:
            rec = await self.store.record(run_id)
            settled = rec is None or rec["status"] not in ("pending", "running")
            for event in (await self.store.events(run_id))[cursor:]:
                yield cursor, event
                cursor += 1
            if settled:
                return
            await asyncio.sleep(poll)

    async def _records(self) -> list[dict[str, Any]]:
        """Every run this API knows: its own, or with replicas the shared store's."""
        if self.node is None:
            return [r.record() for r in self.runs.values()]
        return await self.store.records()

    def get(self, run_id: str, who: Principal) -> Run:
        run = self.runs.get(run_id)
        if run is None or not self._visible(run, who):
            raise KeyError(run_id)
        return run

    async def threads(self, who: Principal) -> list[dict[str, Any]]:
        mine = [
            r for r in await self._records() if (r["tenant"], r["user"]) == (who.tenant, who.user)
        ]
        threads: dict[str, list[dict[str, Any]]] = {}
        for r in sorted(mine, key=lambda r: str(r["created_at"])):
            local = self.runs.get(r["run_id"])
            threads.setdefault(r["thread_id"], []).append(
                local.summary()
                if local is not None
                else {
                    "run_id": r["run_id"],
                    "thread_id": r["thread_id"],
                    "agent": r["agent"],
                    "status": r["status"],
                    "created_at": r["created_at"],
                    "interrupts": r.get("interrupts") or [],
                    "events": None,
                }
            )
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
            if self.node is not None:  # another replica may have created it (Step 38)
                found = await self._idempotent_elsewhere(who, idempotency_key, body)
                if found is not None:
                    return found, False
        # Step 38: a thread stays on the version its first run was given
        thread = thread_id or new_id()
        spec, shadow = await self.releases.choose(agent, thread)
        ctx = RunContext(
            tenant_id=who.tenant,
            user_id=who.user,
            agent_id=spec.name,
            agent_version=spec.version,
            risk_tier=spec.risk_tier,
            thread_id=thread,
        )
        if self.assembly.admit is not None:  # Step 25: only approved agents in staging and prod
            refused = await self.assembly.admit(spec, ctx)
            stable = self.agents[agent]
            if refused and spec.version != stable.version:
                # a candidate not (yet) approved never serves: the stable version does
                spec, ctx = stable, ctx.model_copy(update={"agent_version": stable.version})
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
        try:
            await self.store.save(run.record())
        except IdempotencyRaceError:  # another replica stored the same key a moment earlier
            del self.runs[run.run_id]
            assert idempotency_key is not None
            found = await self._idempotent_elsewhere(who, idempotency_key, body)
            if found is None:
                raise
            return found, False
        if idempotency_key:
            self.idempotency[(who.tenant, who.user, idempotency_key)] = (run.run_id, body)
        await self._hold(run.run_id)
        self._start(run, adapter.invoke(handle, inputs, ctx), first=True)
        if shadow is not None:  # the candidate runs the same input, without side effects
            self._shadows.add(asyncio.create_task(self._shadow(shadow, inputs, who, run)))
        return run, True

    async def _idempotent_elsewhere(self, who: Principal, key: str, body: str) -> Run | None:
        rec = await self.store.find_idempotent(who.tenant, who.user, key)
        if rec is None:
            return None
        if rec.get("body_hash") != body:
            raise ConflictError("this Idempotency-Key was used with a different request")
        peer = await self.locate(rec["run_id"])
        if peer is not None:
            raise RunElsewhereError(peer)
        return self.runs[rec["run_id"]]

    def _disabled_tools(self, agent: str) -> frozenset[str]:
        channel = self.releases.channels.get(agent)
        return channel.flags.disabled_tools if channel is not None else frozenset()

    async def _prepare(
        self, spec: AgentSpec, plan: ExecutablePlan, journal: RunJournal | None
    ) -> tuple[RuntimeAdapter, str]:
        parts = await self.assembly.build(
            spec,
            plan,
            journal,
            spec_path=VERSION_PATHS.get((spec.name, spec.version)) or SPEC_PATHS.get(spec.name),
            disabled_tools=self._disabled_tools(spec.name),
        )
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
        their checkpoints and bring the ones waiting for an approval back to that point. With
        replicas (Step 38), only unfinished runs whose lease this replica can take; finished
        runs are read on demand."""
        actions = []
        for rec in await self.store.records():
            if self.node is not None and (
                str(rec["status"]) in TERMINAL or not await self._hold(rec["run_id"])
            ):
                continue
            action = await self._restore(rec)
            if action:
                actions.append(action)
        return actions

    async def _restore(self, rec: dict[str, Any]) -> str | None:
        """Load one stored run; continue it if it was unfinished. Returns what was done."""
        who = Principal(
            user=rec["user"],
            tenant=rec["tenant"],
            groups=tuple(rec.get("groups", ())),
            roles=tuple(rec.get("roles", ())),
            token="",  # tokens are never stored; on-behalf-of tools need a new one
            client_id=rec.get("client_id", ""),
        )
        spec = self.releases.versions.get(
            (rec["agent"], str(rec.get("agent_version")))
        ) or self.agents.get(rec["agent"])
        status = str(rec["status"])
        active = status in ("pending", "running", "interrupted")
        plan = compile_spec(spec) if spec else None
        ctx = RunContext(
            tenant_id=who.tenant,
            user_id=who.user,
            agent_id=rec["agent"],
            run_id=rec["run_id"],
            thread_id=rec["thread_id"],
        )
        if spec is not None:
            ctx = ctx.model_copy(
                update={"risk_tier": spec.risk_tier, "agent_version": spec.version}
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
            return None
        if adapter is None:
            await self._fail(run, "the service restarted and this run has no checkpoints")
            return f"{run.run_id}: failed (no checkpoints)"
        if status == "interrupted":
            await self._restore_interrupt(run)
            return f"{run.run_id}: waiting for approval again"
        self._start(run, adapter.invoke(handle, run.inputs, ctx))
        return f"{run.run_id}: resumed from checkpoints"

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
        spec = self.spec_of(source)
        ctx = RunContext(
            tenant_id=who.tenant,
            user_id=who.user,
            agent_id=spec.name,
            agent_version=spec.version,
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
        await self._hold(new_run)
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
                self.spec_of(run).risk_tier,
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

    async def inbox(self, who: Principal) -> list[dict[str, Any]]:
        """Approvals this person may decide: same tenant, in the approver group, not their own."""
        out = []
        for rec in await self._records():
            if (
                rec["status"] != "interrupted"
                or rec["tenant"] != who.tenant
                or rec["user"] == who.user
            ):
                continue
            for item in rec.get("interrupts") or []:
                if item.get("reason") == "approval" and in_group(who.groups, item.get("approvers")):
                    out.append(
                        {
                            "run_id": rec["run_id"],
                            "agent": rec["agent"],
                            "requested_by": rec["user"],
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
                            "input": rec.get("inputs"),
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

    # ------------------------------------------------------------ review-after (Step 28)

    @staticmethod
    def sampled(run_id: str, rate: float) -> bool:
        """Deterministic sampling: the same run is always in or out of the sample."""
        digest = int(hashlib.sha256(run_id.encode()).hexdigest()[:8], 16)
        return digest / 0xFFFFFFFF < rate

    async def _sample_for_review(self, run: Run, output: Any) -> None:
        settings = self.spec_of(run).review
        if settings.sample_rate <= 0 or not self.sampled(run.run_id, settings.sample_rate):
            return
        self.reviews[run.run_id] = {
            "run_id": run.run_id,
            "agent": run.agent,
            "tenant": run.owner.tenant,
            "requested_by": run.owner.user,
            "input": run.inputs,
            "output": json_safe(output),
            "reviewers": settings.reviewers,
            "status": "pending",
        }
        await self._audit_event("review.sampled", run.ctx, {"reviewers": settings.reviewers})

    def review_queue(self, who: Principal) -> list[dict[str, Any]]:
        return [
            r
            for r in self.reviews.values()
            if r["status"] == "pending"
            and r["tenant"] == who.tenant
            and r["requested_by"] != who.user
            and in_group(who.groups, r["reviewers"])
        ]

    async def review(
        self, run_id: str, who: Principal, verdict: str, note: str = ""
    ) -> dict[str, Any]:
        item = self.reviews.get(run_id)
        if item is None or item["tenant"] != who.tenant:
            raise KeyError(run_id)
        if not in_group(who.groups, item["reviewers"]) or item["requested_by"] == who.user:
            raise PermissionError(f"reviewer is not in {item['reviewers']}")
        if verdict not in ("ok", "issue"):
            raise ValueError("verdict must be ok or issue")
        if item["status"] != "pending":
            raise ConflictError(f"run {run_id} was already reviewed")
        item.update(status=verdict, reviewed_by=who.user, note=note)
        run = self.runs.get(run_id)
        if run is not None:
            await self._audit_event(
                "review.completed", run.ctx, {"verdict": verdict, "by": who.user, "note": note}
            )
        return item

    # ------------------------------------------------------------ take-over (Step 28)

    TAKEOVER_ROLE = "operator"

    async def take_over(
        self, run: Run, who: Principal, answer: Any = None, note: str = ""
    ) -> dict[str, Any]:
        """A person takes the work from the agent: the agent stops (nothing pending runs), the
        person gets the run's state, and may finish it with their own answer."""
        approver = any(
            in_group(who.groups, i.get("approvers")) for i in run.interrupts if i.get("approvers")
        )
        if who.tenant != run.owner.tenant or not (self.TAKEOVER_ROLE in who.roles or approver):
            raise PermissionError("taking over needs the operator role or the run's approver group")
        if run.status not in ("interrupted", "running", "pending"):
            raise ConflictError(f"run {run.run_id} is {run.status}; nothing to take over")
        if run.task is not None and not run.task.done():
            run.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run.task
        state: dict[str, Any] = {}
        with contextlib.suppress(Exception):
            current = await run.live().get_state(run.run_id)
            state = json_safe(current.values)
            await run.live().cancel(run.run_id)
        handover = {"state": state, "pending": run.interrupts, "input": run.inputs}
        run.status, run.interrupts = "taken_over", []
        output = {"answer": answer, "by": who.user, "note": note} if answer is not None else None
        await self._record(
            run,
            RunEvent(
                type=RunEventType.RUN_FINISHED,
                run_id=run.run_id,
                data={
                    "status": "taken_over",
                    "output": output,
                    "stop_reason": f"taken over by {who.user}",
                },
            ),
        )
        await self.store.save(run.record())
        await self._audit_event(
            "run.taken_over",
            run.ctx,
            {"by": who.user, "note": note, "answered": answer is not None},
        )
        return handover

    # ------------------------------------------------------------ releases (Step 38)

    async def _score(
        self, spec: AgentSpec, run: Run | None, status: str, cost: float, seconds: float, mode: str
    ) -> None:
        """Online evaluation: score a finished run of a scored version; roll the candidate back
        when the gate is breached."""
        if status not in ("succeeded", "failed", "stopped") or run is None:
            return
        if not self.releases.scored(spec, run.run_id):
            return
        ok = status == "succeeded"
        agent_metrics().release_outcome(spec.name, spec.version, mode, ok)
        verdict, rolled = await self.releases.score(
            Outcome(spec.name, spec.version, run.run_id, mode, ok, cost, seconds)
        )
        if rolled and verdict is not None:
            candidate = self.releases.channels[spec.name].candidate
            assert candidate is not None
            agent_metrics().release_rolled_back(spec.name, candidate.version)
            await self._audit_event(
                "release.rolled_back",
                run.ctx,
                {
                    "version": candidate.version,
                    "reason": "; ".join(verdict.reasons),
                    "numbers": verdict.numbers,
                },
            )

    async def _shadow(
        self, spec: AgentSpec, inputs: dict[str, Any], who: Principal, primary: Run
    ) -> None:
        """Run the candidate on the same input in the background: no writes, no approvals
        asked for, nothing the caller sees; only its outcome is scored and audited."""
        ctx = RunContext(
            tenant_id=who.tenant,
            user_id=who.user,
            agent_id=spec.name,
            agent_version=spec.version,
            risk_tier=spec.risk_tier,
        )
        sink = current_sink.set(None)
        caller = current_caller.set(
            Caller(subject=who.subject, via="agent-api", client_id=who.client_id)
        )
        user = subject_token.set(who.token)
        began, status, cost = time.perf_counter(), "failed", 0.0
        try:
            plan = compile_spec(spec)
            parts = await self.assembly.build(
                spec,
                plan,
                spec_path=VERSION_PATHS.get((spec.name, spec.version)),
                shadow=True,
                disabled_tools=self._disabled_tools(spec.name),
            )
            adapter = runtime_for(spec, runner=parts.runner)
            handle = await adapter.build(plan)
            await self.assembly.budgets.open(spec, ctx)
            result = await asyncio.wait_for(
                adapter.invoke(handle, inputs, ctx), timeout=spec.budgets.max_wall_seconds + 5
            )
            if result.status.value != "interrupted":
                result = await self.assembly.budgets.finish(spec, ctx, result, None)
            status = result.status.value
            cost = self.assembly.ledger.total(ctx.run_id).cost_aud
        except Exception as exc:  # a shadow never affects the stable run
            status = f"failed: {type(exc).__name__}"
        finally:
            subject_token.reset(user)
            current_caller.reset(caller)
            current_sink.reset(sink)
        await self._audit_event(
            "release.shadow_run",
            ctx,
            {"version": spec.version, "status": status, "primary_run": primary.run_id},
        )
        shadow_run = Run(
            run_id=ctx.run_id,
            thread_id=ctx.thread_id,
            agent=spec.name,
            owner=who,
            ctx=ctx,
            plan=ExecutablePlan.model_construct(name=spec.name, nodes=(), entry="", exits=()),
            adapter=None,
            handle="",
            inputs={},
        )
        await self._score(
            spec,
            shadow_run,
            "failed" if status.startswith("failed") else status,
            cost,
            time.perf_counter() - began,
            "shadow",
        )

    async def _audit_event(self, kind: str, ctx: RunContext, payload: dict[str, Any]) -> None:
        await self.assembly.audit.append(AuditEvent(type=kind, ctx=ctx, payload=payload))

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

    async def retry(self, run: Run, who: Principal) -> None:
        """Continue a failed run from its checkpoints (Step 33), e.g. after the gateway or a tool
        came back. Completed nodes are answered from the checkpoints, so their side effects are
        not repeated; the node that failed, and everything after it, runs again with the same
        run id, so tool idempotency keys (``<run>:<node>``) still guard any write in flight."""
        if (who.tenant, who.user) != (run.owner.tenant, run.owner.user):
            raise PermissionError("only the requester may retry a run")
        if run.active or run.status != "failed":
            raise ConflictError(f"run {run.run_id} is {run.status}, not failed")
        checkpointer = self.assembly.checkpointer
        spec = self.spec_of(run) if run.agent in self.agents else None
        if checkpointer is None or spec is None:
            raise ConflictError("retrying needs a checkpointer (durable runs)")
        journal = await load_journal(checkpointer, run.ctx)
        if journal is not None:
            # the failed attempt is not replayed: only outcomes that succeeded are kept
            for node, outcomes in list(journal.entries.items()):
                kept = [o for o in outcomes if o.outcome != "error"]
                if kept:
                    journal.entries[node] = kept
                else:
                    del journal.entries[node]
        run.adapter, run.handle = await self._prepare(spec, run.plan, journal)
        run.status, run.interrupts = "running", []
        self._start(run, run.adapter.invoke(run.handle, run.inputs, run.ctx))

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

    # ------------------------------------------------------------ export and retention (Step 37)

    RETENTION_ROLES = frozenset({"privacy-officer", "platform-operator"})

    async def export_subject(self, tenant: str, user: str, who: Principal) -> dict[str, Any]:
        """Access (APP 12): everything the platform holds about one person in one tenant, their
        runs with event logs, their threads and every agent's memories about them. A person may
        export their own; a privacy officer of the tenant anyone's. Audited."""
        own = (who.tenant, who.user) == (tenant, user)
        if not own and (self.PRIVACY_ROLE not in who.roles or who.tenant != tenant):
            raise PermissionError("export needs your own data or the privacy-officer role")
        records = [
            r for r in await self.store.records() if (r["tenant"], r["user"]) == (tenant, user)
        ]
        runs = [r | {"events": await self.store.events(r["run_id"])} for r in records]
        memories: dict[str, list[dict[str, Any]]] = {}
        for name, spec in self.agents.items():
            memory = await self.assembly.memory_factory(spec)
            if memory is None:
                continue
            scope = MemoryScope(tenant_id=tenant, user_id=user, agent_id=name)
            items = [i for i in await memory.store.items(scope) if i.scope.user_id == user]
            if items:
                memories[name] = [i.model_dump(mode="json") for i in items]
        await self.assembly.audit.append(
            AuditEvent(
                type="subject.exported",
                ctx=RunContext(tenant_id=tenant, user_id=who.user, agent_id="agent-api"),
                payload={
                    "subject": user,
                    "requested_by": who.user,
                    "runs": len(runs),
                    "memories": sum(len(v) for v in memories.values()),
                },
            )
        )
        return {
            "tenant": tenant,
            "user": user,
            "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "runs": runs,
            "threads": sorted({r["thread_id"] for r in records}),
            "memories": memories,
        }

    async def apply_retention(self, policy: Mapping[str, Any], who: Principal) -> dict[str, int]:
        """Retention (governance/data-lifecycle.yaml): prune finished runs and their event
        logs, old checkpoints (keeping each thread's newest) and expired memories. Audited."""
        if not self.RETENTION_ROLES & set(who.roles):
            raise PermissionError("retention needs the privacy-officer or platform-operator role")
        rules = policy["retention"]
        finished = tuple(policy.get("finished_statuses") or TERMINAL)
        now = datetime.now(UTC)
        pruned = await self.store.prune(now - timedelta(days=int(rules["runs"]["days"])), finished)
        for run_id in pruned:
            self.runs.pop(run_id, None)
        checkpoints = 0
        cp = self.assembly.checkpointer
        if cp is not None and hasattr(cp, "prune"):
            checkpoints = await cp.prune(
                timedelta(days=int(rules["checkpoints"]["days"])),
                int(rules["checkpoints"].get("keep_last", 1)),
            )
        memories = 0
        for spec in self.agents.values():
            memory = await self.assembly.memory_factory(spec)
            purge = getattr(getattr(memory, "store", None), "purge_expired", None)
            if purge is not None:
                memories += await purge()
        counts = {"runs": len(pruned), "checkpoints": checkpoints, "memories": memories}
        await self.assembly.audit.append(
            AuditEvent(
                type="retention.applied",
                ctx=RunContext(tenant_id=who.tenant, user_id=who.user, agent_id="agent-api"),
                payload={"requested_by": who.user, **counts},
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
        if run.detached:  # another replica drives this run now (Step 38)
            return
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
            spec = self.spec_of(run)
            # Step 30: one invoke_agent span per run (and per resume); everything the run does,
            # model calls, tool calls and team workers, nests under it
            began = time.perf_counter()
            with agent_span(spec.name, spec.version, run.ctx, spec.description) as span:
                # Step 31: the run and its plan in the audit trail (trace id from this span)
                await self._audit_event(
                    "run.started",
                    run.ctx,
                    {"input_digest": digest(run.inputs), "resumed": not first},
                )
                if first:
                    await self._audit_event("plan", run.ctx, plan_summary(run.plan))
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
                usage = self.assembly.ledger.total(run.run_id)
                end_agent(span, result.status.value, usage, result.state.stop_reason)
                await self._audit_event(
                    "run.ended",
                    run.ctx,
                    {
                        "status": result.status.value,
                        "stop_reason": result.state.stop_reason,
                        "output_digest": digest(result.output),
                        "cost_aud": round(usage.cost_aud, 6),
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                        "model_calls": self.assembly.ledger.calls(run.run_id),
                    },
                )
            if result.status.value != "interrupted":
                # Step 30: runs, success, steps, cost per run and tenant, latency, budget trips
                agent_metrics().run_finished(
                    spec.name,
                    run.ctx.tenant_id,
                    result.status.value,
                    time.perf_counter() - began,
                    self.assembly.ledger.calls(run.run_id),
                    usage.cost_aud,
                    result.state.stop_reason,
                )
                # Step 38: online evaluation of the version that served this run
                await self._score(
                    spec,
                    run,
                    result.status.value,
                    usage.cost_aud,
                    time.perf_counter() - began,
                    self.releases.mode_of(spec.name, spec.version),
                )
            run.status = result.status.value
            if run.status == "succeeded":
                await self._sample_for_review(run, result.output)
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
                        self.spec_of(run).risk_tier,
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
            await self._audit_event(
                "run.ended", run.ctx, {"status": "failed", "error": type(exc).__name__}
            )
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
            if not run.detached:
                await self.store.save(run.record())
            if run.status in TERMINAL:
                self.held.discard(run.run_id)
            async with run.changed:
                run.active = False
                run.changed.notify_all()

    async def _finish_cancelled(self, run: Run, reason: str | None = None) -> None:
        if run.detached:  # let go after losing the lease: not a cancellation (Step 38)
            return
        run.status, run.interrupts = "cancelled", []
        await self._audit_event(
            "run.ended", run.ctx, {"status": "cancelled", "stop_reason": reason}
        )
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
