"""Agent versions per environment (Step 38): which version serves, a canary or shadow
candidate, feature flags, online evaluation and automatic rollback.

An environment's releases file (``deploy/releases/<env>.yaml``, mounted at
``AGENT_API_RELEASES``) names, per agent::

    invoice-triage:
      stable: 0.3.0
      candidate: { version: 0.4.0, mode: canary, weight: 10 }  # or mode: shadow
      gate: { min_runs: 20, max_failure_rate_increase_pp: 5 }   # optional; defaults below
      flags: { enabled: true, disabled_tools: [] }              # optional

* **Canary.** ``weight`` percent of new threads run on the candidate (a thread stays on the
  version its first run chose); everyone else gets the stable version.
* **Shadow.** Every run is served by the stable version; ``weight`` percent are also run, in the
  background, on the candidate with the same input. A shadow run never has a side effect: tools
  that write are not called (they answer "shadowed"), it asks nobody for approval, and the
  caller never sees it.
* **Online evaluation.** Every candidate run, and a sample of stable runs (by risk tier: all for
  high), is scored as it ends: did it succeed, what did it cost, how long did it take. The gate
  compares the candidate with the stable version over the window.
* **Automatic rollback.** When the gate is breached the candidate is rolled back at once, on
  every replica (the state is in the shared store), audited and counted. Git still names it as
  the candidate until a pull request removes it; a rolled-back version never serves again, so a
  fix ships as a new version.
* **Feature flags.** ``enabled: false`` switches the agent off (new runs refused);
  ``disabled_tools`` makes the named tools refuse every call.

Durable (Temporal) agents keep a single version per environment here: their in-flight runs
finish on the version they started with through Temporal worker versioning, which is the
documented follow-up (docs/setup/production.md).
"""

from __future__ import annotations

import asyncio
import hashlib
import statistics
import time
from abc import ABC, abstractmethod
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import yaml
from agent_contracts.interfaces import ToolExecutor, ToolRegistry
from agent_contracts.models import (
    RunContext,
    SideEffect,
    ToolCall,
    ToolErrorCode,
    ToolResult,
)
from agent_spec import AgentSpec

MODES = ("canary", "shadow")
# owner policy defaults: the share of stable runs scored online, by risk tier (spec: "online
# evals on a sample" for medium, "on all runs" for high); candidate runs are always scored
STABLE_SAMPLE = {"low": 0.1, "medium": 0.25, "high": 1.0}


@dataclass(frozen=True)
class Gate:
    """When a candidate is rolled back. Rates in percentage points, growth in percent."""

    min_runs: int = 20
    max_failure_rate_increase_pp: float = 5.0
    max_cost_increase_pct: float = 25.0
    max_latency_increase_pct: float = 50.0
    window_minutes: int = 1440

    @classmethod
    def of(cls, data: Mapping[str, Any] | None) -> Gate:
        gate = cls(**dict(data or {}))
        if gate.min_runs < 1 or gate.window_minutes < 1:
            raise ValueError("gate: min_runs and window_minutes must be at least 1")
        return gate


@dataclass(frozen=True)
class Flags:
    enabled: bool = True
    disabled_tools: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Candidate:
    version: str
    mode: str
    weight: int


@dataclass(frozen=True)
class Channel:
    agent: str
    stable: str
    candidate: Candidate | None = None
    gate: Gate = field(default_factory=Gate)
    flags: Flags = field(default_factory=Flags)


@dataclass(frozen=True)
class Outcome:
    agent: str
    version: str
    run_id: str
    mode: str  # stable | canary | shadow
    ok: bool
    cost_aud: float
    seconds: float
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class Verdict:
    decision: str  # waiting | pass | breach
    reasons: tuple[str, ...]
    numbers: dict[str, float | int]


def bucket(key: str) -> int:
    """0-99, the same for the same key on every replica."""
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % 100


def load_releases(
    data: Mapping[str, Any], versions: Mapping[tuple[str, str], AgentSpec]
) -> dict[str, Channel]:
    channels: dict[str, Channel] = {}
    for agent, entry in (data.get("agents") or {}).items():
        stable = str(entry.get("stable") or "")
        if (agent, stable) not in versions:
            raise ValueError(f"{agent}: stable version {stable!r} is not deployed")
        candidate = None
        if entry.get("candidate"):
            c = entry["candidate"]
            candidate = Candidate(
                str(c.get("version")), str(c.get("mode", "canary")), int(c.get("weight", 10))
            )
            if (agent, candidate.version) not in versions:
                raise ValueError(
                    f"{agent}: candidate version {candidate.version!r} is not deployed"
                )
            if candidate.version == stable:
                raise ValueError(f"{agent}: the candidate is the stable version")
            if candidate.mode not in MODES:
                raise ValueError(f"{agent}: candidate mode must be one of {', '.join(MODES)}")
            if not 0 < candidate.weight <= 100:
                raise ValueError(f"{agent}: candidate weight must be 1-100 (percent)")
            if versions[(agent, candidate.version)].runtime.durability.value == "temporal":
                raise ValueError(
                    f"{agent}: durable (Temporal) agents are released whole; canary and shadow"
                    " need Temporal worker versioning (docs/setup/production.md)"
                )
        flags = entry.get("flags") or {}
        channels[agent] = Channel(
            agent,
            stable,
            candidate,
            Gate.of(entry.get("gate")),
            Flags(bool(flags.get("enabled", True)), frozenset(flags.get("disabled_tools") or ())),
        )
    return channels


def evaluate(gate: Gate, stable: list[Outcome], candidate: list[Outcome]) -> Verdict:
    """Compare a candidate's scored runs with the stable version's over the gate's window."""

    def rate(xs: list[Outcome]) -> float:
        return 100.0 * sum(not o.ok for o in xs) / len(xs) if xs else 0.0

    def mean(xs: list[float]) -> float:
        return statistics.fmean(xs) if xs else 0.0

    numbers: dict[str, float | int] = {
        "candidate_runs": len(candidate),
        "stable_runs": len(stable),
        "candidate_failure_pct": round(rate(candidate), 2),
        "stable_failure_pct": round(rate(stable), 2),
        "candidate_cost_aud": round(mean([o.cost_aud for o in candidate]), 6),
        "stable_cost_aud": round(mean([o.cost_aud for o in stable]), 6),
        "candidate_seconds": round(mean([o.seconds for o in candidate]), 3),
        "stable_seconds": round(mean([o.seconds for o in stable]), 3),
    }
    if len(candidate) < gate.min_runs:
        return Verdict("waiting", (f"{len(candidate)} of {gate.min_runs} candidate runs",), numbers)
    reasons = []
    worse = round(rate(candidate) - rate(stable), 2)
    if worse > gate.max_failure_rate_increase_pp:
        reasons.append(
            f"failure rate {numbers['candidate_failure_pct']}% vs {numbers['stable_failure_pct']}%"
            f" (+{worse} pp > {gate.max_failure_rate_increase_pp} pp)"
        )
    for metric, limit in (
        ("cost_aud", gate.max_cost_increase_pct),
        ("seconds", gate.max_latency_increase_pct),
    ):
        base, now = float(numbers[f"stable_{metric}"]), float(numbers[f"candidate_{metric}"])
        if base > 0 and stable:
            growth = round(100.0 * (now - base) / base, 1)
            if growth > limit:
                reasons.append(f"{metric} +{growth}% > {limit}% ({now} vs {base})")
    return Verdict("breach" if reasons else "pass", tuple(reasons), numbers)


# ---------------------------------------------------------------- shared state


class ReleaseState(ABC):
    """Rollbacks and scored outcomes, shared by every replica."""

    @abstractmethod
    async def rolled_back(self, agent: str, version: str) -> str | None:
        """Why this version was rolled back, or None."""

    @abstractmethod
    async def roll_back(self, agent: str, version: str, reason: str) -> bool:
        """Record a rollback; False if it was already rolled back."""

    @abstractmethod
    async def record(self, outcome: Outcome) -> None: ...

    @abstractmethod
    async def outcomes(self, agent: str, version: str, since: datetime) -> list[Outcome]: ...


class MemoryReleaseState(ReleaseState):
    def __init__(self) -> None:
        self.rollbacks: dict[tuple[str, str], str] = {}
        self.scored: list[Outcome] = []

    async def rolled_back(self, agent: str, version: str) -> str | None:
        return self.rollbacks.get((agent, version))

    async def roll_back(self, agent: str, version: str, reason: str) -> bool:
        if (agent, version) in self.rollbacks:
            return False
        self.rollbacks[(agent, version)] = reason
        return True

    async def record(self, outcome: Outcome) -> None:
        if not any(o.run_id == outcome.run_id and o.mode == outcome.mode for o in self.scored):
            self.scored.append(outcome)

    async def outcomes(self, agent: str, version: str, since: datetime) -> list[Outcome]:
        return [
            o for o in self.scored if (o.agent, o.version) == (agent, version) and o.at >= since
        ]


class PostgresReleaseState(ReleaseState):
    """``agent_runtime.release_state`` and ``release_outcomes`` (migration 0010). Outcomes hold
    no content: run id, version, success, cost and duration."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def _conn(self) -> psycopg.AsyncConnection[Any]:
        return await psycopg.AsyncConnection.connect(self._dsn, autocommit=True)

    async def rolled_back(self, agent: str, version: str) -> str | None:
        async with await self._conn() as conn:
            row = await (
                await conn.execute(
                    "select reason from agent_runtime.release_state where agent = %s and"
                    " version = %s and state = 'rolled_back'",
                    (agent, version),
                )
            ).fetchone()
        return str(row[0]) if row else None

    async def roll_back(self, agent: str, version: str, reason: str) -> bool:
        async with await self._conn() as conn:
            cur = await conn.execute(
                "insert into agent_runtime.release_state (agent, version, state, reason)"
                " values (%s, %s, 'rolled_back', %s) on conflict (agent, version) do nothing",
                (agent, version, reason),
            )
        return cur.rowcount == 1

    async def record(self, outcome: Outcome) -> None:
        async with await self._conn() as conn:
            await conn.execute(
                "insert into agent_runtime.release_outcomes (agent, version, run_id, mode, ok,"
                " cost_aud, seconds, at) values (%s, %s, %s, %s, %s, %s, %s, %s)"
                " on conflict (run_id, mode) do nothing",
                (
                    outcome.agent,
                    outcome.version,
                    outcome.run_id,
                    outcome.mode,
                    outcome.ok,
                    outcome.cost_aud,
                    outcome.seconds,
                    outcome.at,
                ),
            )

    async def outcomes(self, agent: str, version: str, since: datetime) -> list[Outcome]:
        async with await self._conn() as conn:
            rows = await (
                await conn.execute(
                    "select run_id, mode, ok, cost_aud, seconds, at from"
                    " agent_runtime.release_outcomes where agent = %s and version = %s and at >= %s",
                    (agent, version, since),
                )
            ).fetchall()
        return [
            Outcome(agent, version, r[0], r[1], r[2], float(r[3]), float(r[4]), r[5]) for r in rows
        ]


# ---------------------------------------------------------------- the releases of one API


class Releases:
    """Which spec version serves each run, and the online gate that can roll a candidate back."""

    CACHE_SECONDS = 5.0

    def __init__(
        self,
        channels: Mapping[str, Channel],
        versions: Mapping[tuple[str, str], AgentSpec],
        state: ReleaseState | None = None,
    ) -> None:
        self.channels = dict(channels)
        self.versions = dict(versions)
        self.state = state or MemoryReleaseState()
        self._cache: dict[tuple[str, str], tuple[float, str | None]] = {}

    @classmethod
    def single(cls, agents: Mapping[str, AgentSpec]) -> Releases:
        """No releases file (dev, tests): each agent's one loaded version is stable."""
        versions = {(s.name, s.version): s for s in agents.values()}
        return cls({s.name: Channel(s.name, s.version) for s in agents.values()}, versions)

    @classmethod
    def from_file(
        cls, path: str | Path, versions: Mapping[tuple[str, str], AgentSpec], state: ReleaseState
    ) -> Releases:
        data = yaml.safe_load(Path(path).read_text()) or {}
        return cls(load_releases(data, versions), versions, state)

    def stable(self) -> dict[str, AgentSpec]:
        return {a: self.versions[(a, c.stable)] for a, c in self.channels.items()}

    async def rolled_back(self, agent: str, version: str) -> str | None:
        hit = self._cache.get((agent, version))
        if hit is not None and hit[0] > time.monotonic():
            return hit[1]
        reason = await self.state.rolled_back(agent, version)
        self._cache[(agent, version)] = (time.monotonic() + self.CACHE_SECONDS, reason)
        return reason

    async def choose(self, agent: str, key: str) -> tuple[AgentSpec, AgentSpec | None]:
        """The spec that serves this run, and a candidate to shadow it with (or None)."""
        channel = self.channels[agent]
        if not channel.flags.enabled:
            raise PermissionError(f"agent {agent} is switched off (feature flag)")
        stable = self.versions[(agent, channel.stable)]
        cand = channel.candidate
        if cand is None or bucket(key) >= cand.weight:
            return stable, None
        if await self.rolled_back(agent, cand.version):
            return stable, None
        candidate = self.versions[(agent, cand.version)]
        return (candidate, None) if cand.mode == "canary" else (stable, candidate)

    def mode_of(self, agent: str, version: str) -> str:
        cand = self.channels[agent].candidate if agent in self.channels else None
        return cand.mode if cand is not None and cand.version == version else "stable"

    def scored(self, spec: AgentSpec, run_id: str) -> bool:
        if self.mode_of(spec.name, spec.version) != "stable":
            return True
        return bucket(f"score|{run_id}") < 100 * STABLE_SAMPLE.get(spec.risk_tier.value, 1.0)

    async def judge(self, agent: str) -> Verdict | None:
        channel = self.channels.get(agent)
        if channel is None or channel.candidate is None:
            return None
        since = datetime.now(UTC) - timedelta(minutes=channel.gate.window_minutes)
        stable, candidate = await asyncio.gather(
            self.state.outcomes(agent, channel.stable, since),
            self.state.outcomes(agent, channel.candidate.version, since),
        )
        return evaluate(channel.gate, stable, candidate)

    async def score(self, outcome: Outcome) -> tuple[Verdict | None, bool]:
        """Record a scored run; returns the gate's verdict and whether this rolled back."""
        await self.state.record(outcome)
        channel = self.channels.get(outcome.agent)
        if channel is None or channel.candidate is None:
            return None, False
        verdict = await self.judge(outcome.agent)
        if verdict is None or verdict.decision != "breach":
            return verdict, False
        rolled = await self.state.roll_back(
            outcome.agent, channel.candidate.version, "; ".join(verdict.reasons)
        )
        self._cache.pop((outcome.agent, channel.candidate.version), None)
        return verdict, rolled

    async def report(self) -> list[dict[str, Any]]:
        out = []
        for agent, channel in sorted(self.channels.items()):
            cand = channel.candidate
            verdict = await self.judge(agent)
            out.append(
                {
                    "agent": agent,
                    "stable": channel.stable,
                    "candidate": None
                    if cand is None
                    else {
                        "version": cand.version,
                        "mode": cand.mode,
                        "weight": cand.weight,
                        "rolled_back": await self.state.rolled_back(agent, cand.version),
                    },
                    "flags": {
                        "enabled": channel.flags.enabled,
                        "disabled_tools": sorted(channel.flags.disabled_tools),
                    },
                    "online_eval": None
                    if verdict is None
                    else {
                        "decision": verdict.decision,
                        "reasons": list(verdict.reasons),
                        **verdict.numbers,
                    },
                }
            )
        return out


# ---------------------------------------------------------------- tool wrappers


class ShadowToolExecutor(ToolExecutor):
    """For shadow runs: tools that write are answered "shadowed" and never called."""

    def __init__(self, inner: ToolExecutor, registry: ToolRegistry) -> None:
        self.inner = inner
        self.registry = registry

    async def execute(self, call: ToolCall, ctx: RunContext) -> ToolResult:
        try:
            tool = await self.registry.get(call.name)
        except KeyError:
            return await self.inner.execute(call, ctx)
        if tool.spec.side_effect is not SideEffect.READ:
            return ToolResult(
                call_id=call.id,
                tool=call.name,
                ok=True,
                output={"shadowed": True, "note": "shadow run: this write was not performed"},
            )
        return await self.inner.execute(call, ctx)


class FlaggedToolExecutor(ToolExecutor):
    """Tools switched off by a feature flag refuse every call."""

    def __init__(self, inner: ToolExecutor, disabled: Collection[str]) -> None:
        self.inner = inner
        self.disabled = frozenset(disabled)

    async def execute(self, call: ToolCall, ctx: RunContext) -> ToolResult:
        if call.name in self.disabled:
            return ToolResult(
                call_id=call.id,
                tool=call.name,
                ok=False,
                error_code=ToolErrorCode.DENIED,
                error=f"tool {call.name} is switched off (feature flag)",
            )
        return await self.inner.execute(call, ctx)
