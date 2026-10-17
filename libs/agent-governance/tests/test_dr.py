"""Step 38: the recovery targets are stated, and the newest recorded drill met them."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_the_newest_drill_met_the_stated_targets() -> None:
    policy = yaml.safe_load((ROOT / "governance" / "dr.yaml").read_text())
    target = policy["data"]["agent-database"]
    assert target["rpo_seconds"] > 0
    assert target["rto_seconds"] > 0
    results = sorted((ROOT / "evals" / "results" / "dr").glob("agent-database-*.json"))
    assert results, "run `just dr-drill` and commit its result"
    newest = json.loads(results[-1].read_text())
    assert newest["passed"] is True
    assert newest["targets"] == {
        "rpo_seconds": target["rpo_seconds"],
        "rto_seconds": target["rto_seconds"],
    }
    assert newest["measured"]["rpo_seconds"] <= target["rpo_seconds"]
    assert newest["measured"]["rto_seconds"] <= target["rto_seconds"]
    assert newest["verification"]["audit_chain_ok"] is True
    assert newest["verification"]["problems"] == []
