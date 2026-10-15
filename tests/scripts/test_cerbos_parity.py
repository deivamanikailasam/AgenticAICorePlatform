"""The Cerbos policy's constants must match the OPA policy data they mirror."""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_cerbos_constants_match_opa_data() -> None:
    data = json.loads((ROOT / "policies/agent/config/data.json").read_text())
    policy = yaml.safe_load((ROOT / "policies/cerbos/tool.yaml").read_text())
    const = policy["resourcePolicy"]["constants"]["local"]
    assert const["clearance"] == data["clearance"]
    assert const["constraints"] == data["constraints"]
    assert sorted(const["kill_tenants"]) == sorted(
        t for t, on in data["kill_switch"]["tenants"].items() if on
    )
    assert sorted(const["kill_agents"]) == sorted(
        a for a, on in data["kill_switch"]["agents"].items() if on
    )
    assert const["approval_groups"] == {
        tool: cfg["approvers"].removeprefix("group:")
        for tool, cfg in data["approvals"]["tools"].items()
    }
