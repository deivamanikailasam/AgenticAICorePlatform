"""Step 35: red-team scenario attacks (``evals/redteam/attacks``) through the run API, with the
obedient adversary as the model and the real OPA.

Run them with ``just redteam`` (which also runs the linked control tests and writes the report);
``pytest -m redteam`` runs only these. Each attack first needs its app's canary to succeed under
the same setup, and OPA to be live with Project A's policies; otherwise the attack fails, so a
broken environment can never pass as a defended one.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from agent_api.redteam_runner import AttackRun, canaries, live_opa, run_scenarios
from agent_contracts.interfaces import PolicyDecisionPoint
from agent_evals.redteam import Attack, Catalogue

pytestmark = pytest.mark.redteam

ROOT = Path(__file__).resolve().parents[3]
CATALOGUE = Catalogue.load(ROOT / "evals" / "redteam")
ATTACKS = {a.id: a for a in CATALOGUE.attacks}
_canary_results: dict[str, AttackRun] = {}


@pytest.fixture(autouse=True)
def _presidio(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The stand-in's Presidio when it is configured, else the loopback double (no PII)."""
    if os.environ.get("P0_PRESIDIO_ANALYZER_URL"):
        yield
        return
    from tests.stack.fakes.presidio_fake import presidio

    with presidio() as url:
        monkeypatch.setenv("P0_PRESIDIO_ANALYZER_URL", url)
        monkeypatch.setenv("P0_PRESIDIO_ANONYMIZER_URL", url)
        yield


@pytest.fixture
async def opa() -> AsyncIterator[PolicyDecisionPoint]:
    pdp = await live_opa()
    yield pdp
    await pdp.aclose()


async def _canary(app: str, pdp: PolicyDecisionPoint) -> AttackRun:
    if app not in _canary_results:
        tasks = canaries().get(app)
        assert tasks, f"no canary for {app} in evals/redteam/canaries.yaml"
        [run] = await run_scenarios(app, tasks[:1], pdp)
        _canary_results[app] = run
    return _canary_results[app]


def _evidence(run: AttackRun) -> dict[str, Any]:
    return run.evidence() | {"error": run.error[:500]}


@pytest.mark.parametrize("attack_id", sorted(ATTACKS))
async def test_attack(attack_id: str, opa: PolicyDecisionPoint, record_property: Any) -> None:
    attack: Attack = ATTACKS[attack_id]
    canary = await _canary(attack.app, opa)
    assert canary.held, (
        f"canary {canary.task.id} failed, so this attack proves nothing: {canary.detail()}"
    )
    [run] = await run_scenarios(attack.app, [attack], opa)
    record_property("evidence", _evidence(run))
    assert run.held, f"{attack.title}: {run.detail()}"
