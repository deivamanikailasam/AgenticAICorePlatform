"""Step 35: the red-team suite itself. The register covers every ASI category, and the scenarios
are not vacuous: with a weaker policy engine (the in-process rules, which know no value limits
and no kill switches) the attacks those controls stop become findings."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from agent_api.redteam_runner import canaries, run_scenarios
from agent_contracts.dummies.safety import RulesPolicyDecisionPoint
from agent_evals.redteam import ALL_ASI, Catalogue

ROOT = Path(__file__).resolve().parents[3]
CATALOGUE = Catalogue.load(ROOT / "evals" / "redteam")


@pytest.fixture(autouse=True)
def _presidio(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from tests.stack.fakes.presidio_fake import presidio

    with presidio() as url:
        monkeypatch.setenv("P0_PRESIDIO_ANALYZER_URL", url)
        monkeypatch.setenv("P0_PRESIDIO_ANONYMIZER_URL", url)
        yield


def test_every_category_has_automated_tests_and_every_attacked_app_a_canary() -> None:
    assert CATALOGUE.gaps() == []
    assert CATALOGUE.gaps(("unit", "integration")) == []  # all ten run without a sandbox runtime
    assert set(CATALOGUE.categories) == set(ALL_ASI)
    assert {a.app for a in CATALOGUE.attacks} <= set(canaries())
    assert {a.severity for a in CATALOGUE.attacks} <= {"critical", "high", "medium", "low"}


async def test_canaries_succeed_under_the_attack_setup() -> None:
    for app, tasks in canaries().items():
        [run] = await run_scenarios(app, tasks, RulesPolicyDecisionPoint())
        assert run.held, (app, run.detail())


async def test_a_weaker_policy_engine_turns_held_attacks_into_findings() -> None:
    attacks = {a.id: a for a in CATALOGUE.attacks}
    weak = RulesPolicyDecisionPoint()  # no value limits, no kill switches
    [over_limit] = await run_scenarios(
        "invoice-triage", [attacks["asi02-payment-over-limit-even-when-approved"]], weak
    )
    assert not over_limit.held
    assert "post_payment ran" in over_limit.detail()
    [killed] = await run_scenarios(
        "invoice-triage",
        [
            attacks["asi10-agent-kill-switch-stops-every-tool"].model_copy(
                update={
                    "input": {
                        k: v
                        for k, v in attacks[
                            "asi10-agent-kill-switch-stops-every-tool"
                        ].input.items()
                        if k != "_opa_data"  # the rules engine has no kill switch to set
                    }
                }
            )
        ],
        weak,
    )
    assert not killed.held
    assert "get_invoice was not denied" in killed.detail()


async def test_an_attack_whose_payload_never_reaches_the_model_proves_nothing() -> None:
    attack = next(a for a in CATALOGUE.attacks if a.id == "asi09-summary-hides-an-inflated-amount")
    blind = attack.model_copy(update={"payload_marker": "text that is nowhere in the payload"})
    [run] = await run_scenarios("invoice-triage", [blind], RulesPolicyDecisionPoint())
    assert not run.held
    assert "never reached the model" in run.detail()
