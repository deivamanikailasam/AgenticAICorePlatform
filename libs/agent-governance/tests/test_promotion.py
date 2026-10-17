"""Step 38: a new agent version goes dev → staging → 10% canary → prod, each step a reviewed
change that passes the promotion rules; every shortcut is refused with a reason."""

from __future__ import annotations

import copy
from itertools import pairwise
from typing import Any

from agent_governance.promotion import Policy, State, problems

POLICY = Policy()
A = "invoice-triage"


def state(
    staging: str | None = "0.3.0",
    prod: str | None = "0.3.0",
    candidate: dict[str, Any] | None = None,
    dev: str = "0.3.0",
    snaps: tuple[str, ...] = ("0.3.0",),
) -> State:
    s = State()
    s.releases["staging"] = {A: {"stable": staging}} if staging else {}
    s.releases["prod"] = {A: {"stable": prod}} if prod else {}
    if candidate:
        s.releases["prod"][A]["candidate"] = candidate
    s.snapshots = {(A, v): f"hash-{v}" for v in snaps}
    s.dev = {A: (dev, f"hash-{dev}")}
    return s


def test_a_new_version_walks_dev_staging_canary_prod() -> None:
    steps = [
        state(),  # 0.3.0 everywhere
        state(dev="0.4.0"),  # merged to main: dev serves 0.4.0
        state(staging="0.4.0", dev="0.4.0", snaps=("0.3.0", "0.4.0")),  # snapshot + staging
        state(  # prod canary at 10%
            staging="0.4.0",
            dev="0.4.0",
            snaps=("0.3.0", "0.4.0"),
            candidate={"version": "0.4.0", "mode": "canary", "weight": 10},
        ),
        state(prod="0.4.0", staging="0.4.0", dev="0.4.0", snaps=("0.3.0", "0.4.0")),  # full
    ]
    for before, after in pairwise(steps):
        assert problems(before, after, POLICY) == []


def test_shortcuts_and_edits_are_refused() -> None:
    staged = state(staging="0.4.0", dev="0.4.0", snaps=("0.3.0", "0.4.0"))
    cases = {
        # straight to prod without a canary
        "reaches prod only through its canary": state(
            prod="0.4.0", staging="0.4.0", dev="0.4.0", snaps=("0.3.0", "0.4.0")
        ),
        # a canary of something staging doesn't serve
        "is not the version staging serves": state(
            staging="0.4.0",
            dev="0.5.0",
            snaps=("0.3.0", "0.4.0", "0.5.0"),
            candidate={"version": "0.5.0", "mode": "canary", "weight": 10},
        ),
        # a canary that starts too big
        "must start as a 10% canary": state(
            staging="0.4.0",
            dev="0.4.0",
            snaps=("0.3.0", "0.4.0"),
            candidate={"version": "0.4.0", "mode": "canary", "weight": 50},
        ),
    }
    for expected, after in cases.items():
        found = problems(staged, after, POLICY)
        assert any(expected in p for p in found), (expected, found)


def test_snapshots_are_immutable_and_come_from_dev() -> None:
    base = state(staging="0.4.0", dev="0.4.0", snaps=("0.3.0", "0.4.0"))
    edited = copy.deepcopy(base)
    edited.snapshots[(A, "0.3.0")] = "changed"
    assert any("is immutable" in p for p in problems(base, edited, POLICY))
    removed = copy.deepcopy(base)
    del removed.snapshots[(A, "0.4.0")]
    found = problems(base, removed, POLICY)
    assert any("was removed but is still released" in p for p in found)
    invented = copy.deepcopy(base)
    invented.snapshots[(A, "9.9.9")] = "hash-9.9.9"
    assert any("is snapshotted from dev" in p for p in problems(base, invented, POLICY))
    tampered = state(dev="0.4.0")
    tampered.snapshots[(A, "0.4.0")] = "not-what-dev-has"
    assert any("differs from agents/" in p for p in problems(state(dev="0.4.0"), tampered, POLICY))


def test_staging_has_no_candidates_and_aborting_a_canary_is_allowed() -> None:
    base = state(staging="0.4.0", dev="0.4.0", snaps=("0.3.0", "0.4.0"))
    with_candidate = copy.deepcopy(base)
    with_candidate.releases["staging"][A]["candidate"] = {"version": "0.4.0", "weight": 10}
    assert any("canaries run in prod only" in p for p in problems(base, with_candidate, POLICY))
    canary = state(
        staging="0.4.0",
        dev="0.4.0",
        snaps=("0.3.0", "0.4.0"),
        candidate={"version": "0.4.0", "mode": "shadow", "weight": 100},
    )
    assert problems(base, canary, POLICY) == [], "shadow mode may cover every run"
    assert problems(canary, base, POLICY) == [], "removing the candidate (abort) is allowed"


def test_a_first_release_goes_to_prod_from_staging() -> None:
    new = state(prod=None)
    assert problems(new, state(), POLICY) == []
    wrong = state(prod="0.4.0", snaps=("0.3.0", "0.4.0"))
    assert any("first release" in p for p in problems(state(prod=None), wrong, POLICY))


def test_the_repository_releases_are_consistent() -> None:
    from scripts.promotion_check import state_at

    head = state_at(None)
    # prod and staging release only agents dev has; a new agent is in dev before its promotion
    assert set(head.releases["prod"]) <= set(head.releases["staging"]) <= set(head.dev)
    assert problems(head, head, POLICY) == []
    for agent, (version, digest) in head.dev.items():
        if head.stable("prod", agent) == version:
            assert head.snapshots[(agent, version)] == digest, f"{agent} snapshot equals dev"
