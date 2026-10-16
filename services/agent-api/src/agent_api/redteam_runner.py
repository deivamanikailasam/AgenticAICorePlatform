"""Run red-team scenario attacks through the run API (Step 35).

Each attack is a real run of a reference app: the platform's assembly (OPA on every tool call,
guardrails, approvals, budgets, audit), the app's real tools, and the obedient adversary as the
model, so whatever the attacker's payload says, the model tries to do. The attack is judged with
the golden-task checks: what holding the line looks like (a payment never made without
approval, a forbidden tool never run, a secret never in the output, the true amount shown to the
approver).

Two things make a "held" result trustworthy:

* **A live policy engine.** OPA fails closed, so an OPA that is down or has no policy loaded
  would deny every call and make every attack look stopped. ``live_opa`` checks first that OPA
  allows a known-good call and denies a known-bad one.
* **Canaries.** Each app has a benign twin (``evals/redteam/canaries.yaml``) run under the same
  setup; it must succeed, or every attack on that app is reported as unproven.
"""

from __future__ import annotations

import functools
import os
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

import httpx
import yaml
from agent_contracts.interfaces import ModelClient, PolicyDecisionPoint
from agent_contracts.models import PolicyEffect, PolicyInput, RunContext, SideEffect
from agent_evals.adversary import ObedientModel
from agent_evals.checks import Observation, Score, score, success
from agent_evals.redteam import Attack
from agent_evals.tasks import Task
from agent_spec import AgentSpec
from agent_tools.policy import OpaPolicyDecisionPoint

from agent_api.eval_runner import AppHarness, Provider, run_tasks

ROOT = Path(__file__).resolve().parents[4]
REDTEAM = ROOT / "evals" / "redteam"


class PolicyEngineNotReady(RuntimeError):  # noqa: N818 - a state, named for what it is
    pass


async def live_opa(url: str | None = None) -> PolicyDecisionPoint:
    """The real OPA with Project A's policies loaded, or an error (never a silent fallback)."""
    pdp = OpaPolicyDecisionPoint(url or os.environ.get("P0_OPA_URL", "http://127.0.0.1:8181"))
    ctx = RunContext(tenant_id="acme", user_id="redteam.preflight", agent_id="preflight")
    read = await pdp.decide(
        PolicyInput(
            ctx=ctx,
            tool="preflight_read",
            side_effect=SideEffect.READ,
            subject={"authenticated": True, "groups": ["/acme/finance"]},
        )
    )
    cross = await pdp.decide(
        PolicyInput(
            ctx=ctx,
            tool="preflight_read",
            side_effect=SideEffect.READ,
            resource_tenant_id="globex",
            subject={"authenticated": True, "groups": ["/acme/finance"]},
        )
    )
    if read.effect is not PolicyEffect.ALLOW or cross.effect is PolicyEffect.ALLOW:
        await pdp.aclose()
        raise PolicyEngineNotReady(
            f"OPA is not answering with Project A's policies (known-good read: {read.effect}, "
            f"{'; '.join(read.reasons)}; cross-tenant read: {cross.effect}). Run "
            "`just provision-policies` against a running OPA."
        )
    return pdp


@asynccontextmanager
async def opa_data(patch: Mapping[str, Any] | None) -> AsyncIterator[None]:
    """Set OPA data documents for one attack (e.g. a kill switch) and put the old ones back.
    ``patch`` maps a data path (``agent/config/kill_switch``) to its value for the attack."""
    if not patch:
        yield
        return
    url = os.environ.get("P0_OPA_URL", "http://127.0.0.1:8181").rstrip("/")
    async with httpx.AsyncClient(base_url=url, timeout=5) as c:
        saved = {}
        for path, value in patch.items():
            saved[path] = (await c.get(f"/v1/data/{path}")).json().get("result")
            (await c.put(f"/v1/data/{path}", json=value)).raise_for_status()
        try:
            yield
        finally:
            for path, value in saved.items():
                await c.put(f"/v1/data/{path}", json=value if value is not None else {})


def harness(app: str) -> AppHarness:
    found = {e.name: e for e in entry_points(group="agent_platform.eval_apps")}
    if app not in found:
        raise KeyError(f"no eval harness for {app!r}")
    made: AppHarness = found[app].load()()
    return made


@dataclass
class AttackRun:
    attack: Attack | None
    task: Task
    observation: Observation
    scores: list[Score]
    models: list[ObedientModel]
    error: str

    @property
    def held(self) -> bool:
        return success(self.scores) and not self.unproven()

    def unproven(self) -> str:
        """An attack whose payload never reached the model and was not blocked by a guardrail
        tested nothing."""
        marker = self.attack.payload_marker if self.attack else None
        if not marker:
            return ""
        if any(m.saw(marker) for m in self.models) or self.observation.guardrail_blocks:
            return ""
        return f"payload {marker!r} never reached the model and no guardrail blocked it"

    def detail(self) -> str:
        failed = [f"{s.metric}: {s.detail}" for s in self.scores if not s.passed]
        if self.unproven():
            failed.append(self.unproven())
        if self.error and failed:
            failed.append(f"run error: {self.error}")
        return "; ".join(failed) or "held"

    def evidence(self) -> dict[str, Any]:
        obs = self.observation
        return {
            "status": obs.status,
            "tool_calls": [(c.tool, c.ok, c.error_code) for c in obs.tool_calls],
            "denied": obs.denied,
            "approvals_requested": obs.approval_arguments,
            "guardrail_blocks": obs.guardrail_blocks,
            "delegations": obs.delegations,
            "payload_reached_model": bool(
                self.attack
                and self.attack.payload_marker
                and any(m.saw(self.attack.payload_marker) for m in self.models)
            ),
        }


def adversary(
    fallback: dict[str, Any] | str | None = None,
    prelude: Sequence[Sequence[Any]] = (),
    by_agent: Mapping[str, Any] | None = None,
) -> tuple[Provider, list[ObedientModel]]:
    """One obedient model per agent in the run; ``by_agent`` gives a team member (by spec name
    or its last part, e.g. ``billing``) its own fallback."""
    models: list[ObedientModel] = []

    def make(spec: AgentSpec) -> ModelClient:
        own = (by_agent or {}).get(spec.name, (by_agent or {}).get(spec.name.rsplit("-", 1)[-1]))
        chosen = own if own is not None else fallback
        model = ObedientModel(
            chosen if chosen is not None else "I can't help with that.",
            spec.name,
            [(str(name), dict(args)) for name, args in prelude],
        )
        models.append(model)
        return model

    return Provider("adversary", make), models


async def run_scenarios(
    app: str,
    items: Sequence[Attack | Task],
    pdp: PolicyDecisionPoint,
    fallback: dict[str, Any] | str | None = None,
) -> list[AttackRun]:
    """Run attacks (or canary tasks) on one app, each with its own obedient model."""
    out = []
    for item in items:
        attack = item if isinstance(item, Attack) else None
        task = item.task() if isinstance(item, Attack) else item
        provider, models = adversary(
            task.input.get("_fallback", fallback),
            task.input.get("_prelude", ()),
            task.input.get("_fallback_by_agent"),
        )
        says_yes = attack.approve if attack else True
        async with opa_data(task.input.get("_opa_data")):
            [(task, obs, error)] = await run_tasks(
                harness(app), [task], provider, functools.partial(_answer, says_yes), pdp=pdp
            )
        out.append(AttackRun(attack, task, obs, score(task, obs), models, error))
    return out


def _answer(yes: bool, task: Task) -> bool:
    """What the approver says when the run asks: the attack's ``approve`` (canaries: yes)."""
    return yes


def canaries() -> dict[str, list[Task]]:
    doc = yaml.safe_load((REDTEAM / "canaries.yaml").read_text()) or []
    out: dict[str, list[Task]] = {}
    for raw in doc:
        task = Task.model_validate(raw)
        out.setdefault(task.app, []).append(task)
    return out


__all__ = [
    "AttackRun",
    "PolicyEngineNotReady",
    "canaries",
    "harness",
    "live_opa",
    "run_scenarios",
]
