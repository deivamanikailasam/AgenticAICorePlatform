"""Promotion rules (Step 38): a new agent version goes dev → staging → 10% canary → prod, and only
by pull request.

* **dev** deploys ``agents/`` at main's head; merging the change is the promotion to dev.
* A version leaves dev as a **snapshot**, ``deploy/specs/<agent>/<version>/`` (``agent.yaml`` and
  ``workers/``), copied from ``agents/<agent>/`` while that is the version there. A snapshot never
  changes afterwards: a fix is a new version.
* **staging** (``deploy/releases/staging.yaml``) serves snapshots as its stable versions; it has
  no candidates.
* **prod** (``deploy/releases/prod.yaml``) takes a new version first as a **candidate**: the
  version staging serves, as a canary at the policy's weight (10%) or in shadow. It becomes prod's
  stable version only by replacing the candidate it was, after its online evaluation passed
  (checked by ``just promote --full`` against the running API).
* An agent's first prod release has no stable version to compare a canary with, so it goes in
  as stable directly, from staging.

``problems(base, head, policy)`` compares the deployment state of a pull request's base and head
and says what breaks these rules. CI runs it on every pull request; together with the approvals
on ``deploy/releases/prod.yaml`` and ``deploy/specs/`` (governance paths) it makes promotion
reviewable and ordered.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

ENVS = ("staging", "prod")


@dataclass(frozen=True)
class Policy:
    canary_weight: int = 10
    modes: tuple[str, ...] = ("canary", "shadow")

    @classmethod
    def of(cls, data: Mapping[str, Any] | None) -> Policy:
        d = dict(data or {})
        canary = d.get("canary") or {}
        return cls(
            int(canary.get("weight", 10)), tuple(canary.get("modes") or ("canary", "shadow"))
        )


@dataclass
class State:
    """The deployment as one commit has it."""

    releases: dict[str, dict[str, Any]] = field(default_factory=dict)  # env -> agent -> entry
    snapshots: dict[tuple[str, str], str] = field(default_factory=dict)  # (agent, version) -> hash
    dev: dict[str, tuple[str, str]] = field(default_factory=dict)  # agent -> (version, hash)

    def stable(self, env: str, agent: str) -> str | None:
        entry = self.releases.get(env, {}).get(agent) or {}
        return str(entry["stable"]) if entry.get("stable") else None

    def candidate(self, env: str, agent: str) -> dict[str, Any] | None:
        entry = self.releases.get(env, {}).get(agent) or {}
        cand = entry.get("candidate")
        return dict(cand) if cand else None


def tree_hash(files: Mapping[str, bytes]) -> str:
    """One version's files (``agent.yaml``, ``workers/<name>.yaml``) as one digest."""
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode() + b"\0" + hashlib.sha256(files[name]).digest())
    return h.hexdigest()


def problems(base: State, head: State, policy: Policy) -> list[str]:
    out: list[str] = []
    # snapshots are immutable; a version still released can't be removed
    referenced = {
        (agent, str(v))
        for env in ENVS
        for agent, entry in head.releases.get(env, {}).items()
        for v in (entry.get("stable"), (entry.get("candidate") or {}).get("version"))
        if v
    }
    for key, digest in base.snapshots.items():
        if key not in head.snapshots:
            if key in referenced:
                out.append(f"deploy/specs/{key[0]}/{key[1]} was removed but is still released")
        elif head.snapshots[key] != digest:
            out.append(
                f"deploy/specs/{key[0]}/{key[1]} changed: a released version is immutable;"
                " release the change as a new version"
            )
    for key, digest in head.snapshots.items():
        if key in base.snapshots:
            continue
        agent, version = key
        dev = head.dev.get(agent)
        if dev is None or dev[0] != version:
            out.append(
                f"deploy/specs/{agent}/{version} is new but agents/{agent} is not at {version}:"
                " a version is snapshotted from dev (main)"
            )
        elif dev[1] != digest:
            out.append(f"deploy/specs/{agent}/{version} differs from agents/{agent} at {version}")
    for agent, version in sorted(referenced):
        if (agent, version) not in head.snapshots:
            out.append(
                f"{agent} {version} is released but deploy/specs/{agent}/{version} is missing"
            )
    # staging: stable versions only
    for agent in sorted(head.releases.get("staging", {})):
        if head.candidate("staging", agent):
            out.append(f"staging: {agent} has a candidate; canaries run in prod only")
    # prod: candidates come from staging; stable only through the candidate it was
    for agent in sorted(set(head.releases.get("prod", {})) | set(base.releases.get("prod", {}))):
        out += _prod(agent, base, head, policy)
    return out


def _prod(agent: str, base: State, head: State, policy: Policy) -> list[str]:
    out = []
    was_stable, now_stable = base.stable("prod", agent), head.stable("prod", agent)
    was_cand, now_cand = base.candidate("prod", agent), head.candidate("prod", agent)
    staging = head.stable("staging", agent)
    if now_cand and now_cand != was_cand:
        version = str(now_cand.get("version"))
        if (was_cand or {}).get("version") != version and version != staging:
            out.append(
                f"prod: candidate {agent} {version} is not the version staging serves"
                f" ({staging}); promote it to staging first"
            )
        mode = str(now_cand.get("mode", "canary"))
        weight = int(now_cand.get("weight", policy.canary_weight))
        if mode not in policy.modes:
            out.append(f"prod: {agent} candidate mode {mode!r} is not one of {policy.modes}")
        if mode == "canary" and not was_cand and weight != policy.canary_weight:
            out.append(
                f"prod: {agent} {version} must start as a {policy.canary_weight}% canary"
                f" (asked {weight}%)"
            )
    if now_stable and now_stable != was_stable:
        if was_stable is None:
            if now_stable != staging:
                out.append(
                    f"prod: first release of {agent} must be the version staging serves"
                    f" ({staging}), not {now_stable}"
                )
        elif (was_cand or {}).get("version") != now_stable:
            out.append(
                f"prod: {agent} {now_stable} reaches prod only through its canary"
                f" (prod's candidate was {(was_cand or {}).get('version')})"
            )
        elif now_cand and str(now_cand.get("version")) == now_stable:
            out.append(f"prod: {agent} {now_stable} is both stable and candidate")
    return out
