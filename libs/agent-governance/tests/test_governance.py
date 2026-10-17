"""Step 32: agent, tool and system cards, lineage, the AI-use register, CODEOWNERS by risk tier
and the high-risk merge gate."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from agent_contracts.models import RiskTier
from agent_governance import cards, codeowners, lineage, register
from agent_governance.codeowners import Review, decide
from agent_governance.inventory import Inventory, load

ROOT = Path(__file__).resolve().parents[3]
APPROVERS = {
    "default": ["@owner"],
    "tiers": {"low": ["@dev"], "medium": ["@lead"], "high": ["@risk-officer", "@org/risk"]},
    "governance_paths": ["/governance/", "/.github/"],
}
HIGH = "risk_tier: high\nname: x\n"
LOW = "risk_tier: low\nname: x\n"


@pytest.fixture(scope="module")
def inv() -> Inventory:
    return load(ROOT, {})


def high(inv: Inventory, name: str) -> Inventory:
    entries = [
        replace(e, spec=e.spec.model_copy(update={"risk_tier": RiskTier.HIGH}))
        if e.spec.name == name
        else e
        for e in inv.entries
    ]
    return Inventory(inv.root, entries, inv.platform, inv.tool_cards, APPROVERS, inv.eval_results)


# ---------------------------------------------------------------- cards


def test_every_spec_gets_an_agent_card_with_the_required_fields(inv: Inventory) -> None:
    assert len(inv.entries) == 21  # 8 top-level specs and 13 workers
    for e in inv.entries:
        card = cards.agent_card(e, inv)
        for field in (
            "purpose",
            "owner",
            "risk_tier",
            "autonomy",
            "data",
            "limitations",
            "eval_results",
        ):
            assert card[field], (e.spec.name, field)
        assert "tools" in card  # a team's own tool list may be empty: its workers hold the tools
    card = cards.agent_card(inv.by_name("invoice-triage"), inv)
    assert (card["owner"], card["risk_tier"], card["autonomy"]) == (
        "finance-platform",
        "medium",
        "hybrid",
    )
    pay = next(t for t in card["tools"] if t["name"] == "post_payment")
    assert pay["approval"] == "group:finance-approvers"
    assert card["human_oversight"] == ["post_payment: group:finance-approvers"]
    assert card["guardrails"][:2] == ["prompt_injection", "secret_leak"]  # baseline, always on
    assert any("Stops at 25 steps" in lim for lim in card["limitations"])


def test_eval_results_are_cited_only_from_a_recorded_run(inv: Inventory) -> None:
    entry = inv.by_name("invoice-triage")
    assert (
        cards.eval_results(entry.spec, inv) == "No recorded eval run (suite: evals/invoice-triage)"
    )
    recorded = Inventory(
        inv.root,
        inv.entries,
        inv.platform,
        inv.tool_cards,
        inv.approvers,
        {
            "invoice-triage": {
                "passed": 11,
                "total": 12,
                "dataset": "evals/invoice-triage",
                "model": "local-qwen-fast",
                "date": "2026-10-15",
            }
        },
    )
    assert cards.eval_results(entry.spec, recorded) == (
        "11/12 passed on evals/invoice-triage (12 cases, model local-qwen-fast, 2026-10-15)"
    )


def test_a_system_is_as_risky_as_its_riskiest_agent(inv: Inventory) -> None:
    assert cards.system_card(inv.by_name("research-report"), inv)["risk_tier"] == "low"
    raised = high(inv, "research-report-writer")
    card = cards.system_card(raised.by_name("research-report"), raised)
    assert card["risk_tier"] == "high"
    assert card["agents"][0]["name"] == "research-report"
    assert {t["ref"] for t in card["tools"]} >= {
        "mcp://rag-core/search",
        "local://platform/calculator",
    }


def test_unlisted_mcp_servers_are_called_out_not_guessed(inv: Inventory) -> None:
    card = cards.agent_card(inv.by_name("invoice-triage"), inv)
    erp = next(t for t in card["tools"] if t["name"] == "get_invoice")
    assert erp["side_effect"] == "undeclared"
    assert any("not in platform.yaml's allowlist: erp" in lim for lim in card["limitations"])


# ---------------------------------------------------------------- lineage and register


def test_lineage_hashes_match_the_audit_trail_versions(inv: Inventory) -> None:
    from agent_audit.stamping import spec_digest

    graph = lineage.lineage(inv)
    systems = {s["system"]: s for s in graph["systems"]}
    assert set(systems) == {e.system for e in inv.systems()}
    team = {a["agent"]: a for a in systems["research-report"]["agents"]}
    assert set(team) == {
        "research-report",
        "research-report-researcher",
        "research-report-analyst",
        "research-report-writer",
    }
    entry = inv.by_name("invoice-triage")
    node = systems["invoice-triage"]["agents"][0]
    assert node["spec"]["hash"] == spec_digest(entry.spec)  # the audit event's spec_hash
    assert node["prompts"][0]["node"] == "resolve_exceptions"
    assert {d["kind"] for d in node["datasets"]} == {
        "eval suite",
        "long-term memory",
        "retrieval corpus",
    }
    assert "```mermaid" in lineage.render(graph)


def test_the_register_has_one_entry_per_agent(inv: Inventory) -> None:
    entries = register.register(inv)["entries"]
    assert len(entries) == len(inv.entries)
    assert len({e["id"] for e in entries}) == len(entries)
    pay = next(e for e in entries if e["agent"] == "invoice-triage")
    assert pay["risk_tier"] == "medium"
    assert pay["human_oversight"] == ["post_payment: group:finance-approvers"]


# ---------------------------------------------------------------- CODEOWNERS


def test_codeowners_assigns_each_spec_the_approvers_of_its_tier(inv: Inventory) -> None:
    text = codeowners.render(high(inv, "research-report-writer"))
    lines = text.splitlines()
    assert lines[3] == "* @owner"  # the default comes first: later lines win
    assert "/agents/research-report/workers/writer.yaml @risk-officer @org/risk  # high" in lines
    assert "/agents/invoice-triage/agent.yaml @lead  # medium" in lines
    assert "/apps/invoice-triage/ @lead  # medium" in lines
    assert "/agents/hello-workflow/agent.yaml @dev  # low" in lines
    assert lines.index("/governance/ @risk-officer @org/risk") > lines.index("* @owner")


# ---------------------------------------------------------------- the merge gate


def approve(user: str, commit: str = "head", state: str = "APPROVED") -> Review:
    return Review(user, state, commit)


@pytest.mark.parametrize(
    ("changed", "reviews", "ok"),
    [
        # a low-risk spec change needs no high approver
        ([("agents/a/agent.yaml", LOW, LOW.replace("x", "y"))], [], True),
        # high-risk spec changed, nobody approved
        ([("agents/a/agent.yaml", HIGH, HIGH + "description: d\n")], [], False),
        # a named approver approved the latest commit
        ([("agents/a/agent.yaml", HIGH, HIGH + "v: 1\n")], [approve("risk-officer")], True),
        # approval of an older commit doesn't count
        ([("agents/a/agent.yaml", HIGH, HIGH + "v: 1\n")], [approve("risk-officer", "old")], False),
        # someone who isn't a named approver
        ([("agents/a/agent.yaml", HIGH, HIGH + "v: 1\n")], [approve("dev")], False),
        # approved, then changes requested by the same person: the latest review counts
        (
            [("agents/a/workers/w.yaml", HIGH, HIGH + "v: 1\n")],
            [approve("risk-officer"), approve("risk-officer", state="CHANGES_REQUESTED")],
            False,
        ),
        # raising a spec to high needs the high approver
        ([("agents/a/agent.yaml", LOW, HIGH)], [], False),
        # lowering a high spec does too (otherwise lower first, then change freely)
        ([("agents/a/agent.yaml", HIGH, LOW)], [], False),
        # deleting a high-risk spec
        ([("agents/a/agent.yaml", HIGH, None)], [], False),
        # governance files: the approvers list can't be changed without the high approvers
        ([("governance/approvers.yaml", "a", "b")], [], False),
        ([(".github/workflows/governance.yml", "a", "b")], [approve("risk-officer")], True),
        # an unreadable spec is treated as high
        ([("agents/a/agent.yaml", LOW, "risk_tier: [")], [], False),
        # not a spec: a file named agent.yaml elsewhere
        ([("docs/agent.yaml", HIGH, HIGH + "v: 1\n")], [], True),
    ],
)
def test_the_gate(changed: list, reviews: list[Review], ok: bool) -> None:
    d = decide(changed, reviews, "head", "author", APPROVERS)
    assert d.ok is ok, d


def test_authors_cannot_approve_their_own_high_risk_change() -> None:
    changed = [("agents/a/agent.yaml", HIGH, HIGH + "v: 1\n")]
    d = decide(changed, [approve("risk-officer")], "head", "risk-officer", APPROVERS)
    assert not d.ok
    assert "can't approve their own change" in d.reason
    assert "@risk-officer" in d.reason
    assert d.needs == ["agents/a/agent.yaml is high-risk (high -> high)"]


def test_the_cards_on_disk_are_current() -> None:
    from scripts.cards import main

    assert main(["--check"]) == 0


def test_a_stale_or_orphaned_card_fails_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    import scripts.cards as gen

    for d in (
        "agents",
        "apps",
        "governance",
        "cards",
        "docs/cards",
        "docs/governance",
        ".github",
        "evals",
    ):
        if (ROOT / d).exists():
            shutil.copytree(
                ROOT / d, tmp_path / d, ignore=shutil.ignore_patterns("__pycache__", ".venv")
            )
    shutil.copy(ROOT / "platform.yaml", tmp_path / "platform.yaml")
    monkeypatch.setattr(gen, "ROOT", tmp_path)
    assert gen.main(["--check"]) == 0
    spec = tmp_path / "agents" / "hello-workflow" / "agent.yaml"
    # (a high-risk spec must name an eval suite)
    text = spec.read_text().replace("risk_tier: low", "risk_tier: high")
    spec.write_text(text + "evals: { suite: evals/hello-workflow }\n")
    assert gen.main(["--check"]) == 1  # the card, register and CODEOWNERS no longer match
    assert gen.main([]) == 0
    assert gen.main(["--check"]) == 0
    assert "@deivamanikailasam  # high" in (tmp_path / ".github" / "CODEOWNERS").read_text()
    (tmp_path / "docs" / "cards" / "agents" / "gone.md").write_text("old")
    assert gen.main(["--check"]) == 1  # an orphan
